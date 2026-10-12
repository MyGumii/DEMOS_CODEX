(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.NotionSyncedClipboard=api;})(typeof globalThis==='object'?globalThis:this,function(){
  'use strict';
  const MIME='application/x-code-assist-synced-reference+json',CONTRACT='code_assist.notion.synced_clipboard.v1',MAX_BYTES=16384;
  const exact=(value,keys)=>value&&typeof value==='object'&&!Array.isArray(value)&&Object.keys(value).length===keys.length&&keys.every(key=>Object.hasOwn(value,key));
  const id=value=>typeof value==='string'&&/^[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}$/.test(value);
  const same=(a,b)=>a?.document_id===b?.document_id&&a?.block_id===b?.block_id;
  const escape=value=>value.replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');
  function validate(value,origin){
    if(!exact(value,['version','contract','application_origin','source'])||value.version!==1||value.contract!==CONTRACT
        ||!exact(value.source,['workspace_id','document_id','block_id'])||!Object.values(value.source).every(id))throw Error('동기화 복사 형식이 올바르지 않습니다.');
    let url;try{url=new URL(value.application_origin);}catch{throw Error('동기화 복사 주소가 올바르지 않습니다.');}
    if(!['http:','https:'].includes(url.protocol)||url.username||url.password||url.origin!==value.application_origin||value.application_origin!==origin)throw Error('다른 앱에서 복사한 동기화 블록은 붙여넣을 수 없습니다.');
    return {version:1,contract:CONTRACT,application_origin:origin,source:{...value.source}};
  }
  function decode(encoded,origin){
    if(typeof encoded!=='string'||new TextEncoder().encode(encoded).length>MAX_BYTES)throw Error('동기화 복사 형식이 너무 큽니다.');
    let value;try{value=JSON.parse(encoded);}catch{throw Error('동기화 복사 형식이 올바르지 않습니다.');}
    return validate(value,origin);
  }
  function fromRead(data,instance,original,origin){
    if(data?.contract!=='code_assist.notion.synced_block.v1'||!same(data.instance,instance)||!same(data.origin,original)
        ||data.block?.id!==original.block_id||data.block.type!=='synced_block'||data.block.props?.synced_from!==null)throw Error('동기화 원본이 변경되었습니다.');
    const witnesses=data.witnesses?.filter(item=>item.document_id===original.document_id);
    if(witnesses?.length!==1)throw Error('동기화 원본 위치를 확인할 수 없습니다.');
    return validate({version:1,contract:CONTRACT,application_origin:origin,source:{workspace_id:witnesses[0].workspace_id,...original}},origin);
  }
  function read(clipboard){
    const encoded=clipboard?.getData(MIME);if(encoded||Array.from(clipboard?.types||[]).includes(MIME))return {encoded:encoded||''};
    const html=clipboard?.getData('text/html')||'';
    if(!html.includes('data-notion-synced-reference'))return null;
    if(html.length>MAX_BYTES*4)return {encoded:''};
    const template=document.createElement('template');template.innerHTML=html;
    return {encoded:template.content.querySelector('[data-notion-synced-reference]')?.getAttribute('data-notion-synced-reference')||''};
  }
  function formats(value){
    const descriptor=validate(value,value.application_origin),encoded=JSON.stringify(descriptor);
    const text=`${descriptor.application_origin}/page/${encodeURIComponent(descriptor.source.document_id)}#block-${encodeURIComponent(descriptor.source.block_id)}`;
    return {[MIME]:encoded,'text/html':`<span data-notion-synced-reference="${escape(encoded)}">${escape(text)}</span>`,'text/plain':text};
  }
  function write(clipboard,value){
    if(!clipboard)throw Error('클립보드를 사용할 수 없습니다.');
    for(const [type,text] of Object.entries(formats(value)))clipboard.setData(type,text);
  }
  async function copy(value,{document:doc,navigator:nav,ClipboardItem:Item,Blob:BlobType,current=()=>true}){
    if(!current())throw Error('복사할 화면이 변경되었습니다.');
    let copied=false;
    const listener=event=>{if(!current())return;event.preventDefault();event.stopImmediatePropagation();try{write(event.clipboardData,value);copied=true;}catch{}};
    // A selected block's ordinary copy handler must not append its cached body
    // to this clipboard operation in a second MIME format.
    doc.addEventListener('copy',listener,true);
    try{if(doc.execCommand('copy')&&copied&&current())return;}catch{}
    finally{doc.removeEventListener('copy',listener,true);}
    if(!current())throw Error('복사할 화면이 변경되었습니다.');
    if(!nav?.clipboard?.write||!Item)throw Error('클립보드에 복사하지 못했습니다. 다시 시도해 주세요.');
    const data=formats(value);
    // HTML carries the same bounded descriptor where custom clipboard MIME is
    // unavailable. No source body, credentials or read grant is serialized.
    try{await nav.clipboard.write([new Item({'text/html':new BlobType([data['text/html']],{type:'text/html'}),'text/plain':new BlobType([data['text/plain']],{type:'text/plain'})})]);}
    catch{throw Error('클립보드에 복사하지 못했습니다. 다시 시도해 주세요.');}
    if(!current())throw Error('복사할 화면이 변경되었습니다.');
  }
  return {MIME,CONTRACT,MAX_BYTES,validate,decode,fromRead,read,formats,write,copy};
});

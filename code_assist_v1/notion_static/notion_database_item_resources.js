(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.NotionDatabaseItemResources=api;})(typeof globalThis==='object'?globalThis:this,function(){
  'use strict';
  const types=new Set(['image','file','video','audio','pdf','bookmark','embed']);
  const labels={image:'이미지',file:'파일',video:'동영상',audio:'오디오',pdf:'PDF',bookmark:'웹 북마크',embed:'임베드'};
  const accepts={image:'image/*',video:'video/*',audio:'audio/*',pdf:'application/pdf',file:''};
  function url(value){
    if(typeof value!=='string'||!value.trim()||/[\u0000-\u0020\\]/.test(value.trim()))return '';
    try{const parsed=new URL(value.trim());return ['http:','https:'].includes(parsed.protocol)&&!parsed.username&&!parsed.password?parsed.href:'';}catch{return '';}
  }
  const signature=block=>JSON.stringify([block?.type,block?.props?.file_id||'',block?.props?.url||'']);
  function attachment(block,source){
    const props={...block.props};delete props.file_id;delete props.url;delete props.original_name;delete props.name;
    if(source.file_id){props.file_id=source.file_id;props.name=String(source.name||'첨부파일');}
    else{const link=url(source.url);if(!link)throw Error('올바른 웹 주소를 입력해 주세요.');props.url=link;}
    return {...block,props};
  }
  function render(block,host){
    if(!types.has(block.type))return '';
    const e=host.escape,props=block.props||{},label=labels[block.type],id=e(block.id);
    const source=props.file_id?host.fileUrl(String(props.file_id)):url(props.url);
    const name=String(props.name||props.original_name||label),open=source?`<a href="${e(source)}" target="_blank" rel="noopener noreferrer" aria-label="${e(label)} 열기">↗</a>`:'';
    let media='';
    if(source){
      if(block.type==='image')media=`<img src="${e(source)}" alt="${e(block.content||name)}" loading="lazy" draggable="false">`;
      else if(['video','audio'].includes(block.type))media=`<${block.type} src="${e(source)}" controls preload="metadata"></${block.type}>`;
      else if(block.type==='pdf')media=`<iframe data-item-pdf="${id}" title="${e(name)}" hidden></iframe><p data-item-pdf-status="${id}" role="status">PDF를 불러오는 중…</p>`;
      else if(block.type==='embed')media=`<iframe src="${e(source)}" title="${e(name)}" loading="lazy" sandbox="allow-scripts allow-popups" referrerpolicy="no-referrer"></iframe>`;
      else media=`<a class="item-resource-link" href="${e(source)}" target="_blank" rel="noopener noreferrer"><span aria-hidden="true">${block.type==='bookmark'?'↗':'↓'}</span><span>${e(props.file_id?name:source)}</span></a>`;
    }else media=host.editable?`<button type="button" class="item-resource-empty" data-item-resource="${id}">${e(label)} 추가</button>`:`<div class="item-resource-empty">${e(label)}이 지정되지 않았습니다.</div>`;
    const controls=source?`<div class="item-resource-tools">${open}${host.editable?`<button type="button" data-item-resource="${id}" aria-label="${e(label)} 바꾸기">바꾸기</button>`:''}</div>`:'';
    const caption=host.editable||block.content?`<div class="item-body-text item-resource-caption" data-body-text="${id}" data-body-caption="true" contenteditable="${host.editable}" role="textbox" aria-multiline="true" aria-label="${e(label)} 캡션" data-placeholder="캡션 추가">${host.html(block)}</div>`:'';
    return `<div class="item-resource" data-resource-kind="${block.type}">${media}${controls}${caption}</div>`;
  }
  async function pdfPreview(source,signal,fetcher=fetch){
    const limit=64*1024*1024,response=await fetcher(source,{signal,credentials:'same-origin'});
    if(!response.ok)throw Error('PDF를 불러오지 못했습니다.');
    if(response.headers.get('content-type')?.split(';')[0].trim().toLowerCase()!=='application/pdf')throw Error('PDF 형식의 파일인지 확인해 주세요.');
    if(Number(response.headers.get('content-length'))>limit){await response.body?.cancel();throw Error('큰 PDF는 새 창에서 열어 주세요.');}
    const reader=response.body?.getReader();if(!reader)throw Error('PDF 미리보기를 지원하지 않는 브라우저입니다.');
    const chunks=[];let size=0;
    try{while(true){const {value,done}=await reader.read();if(done)break;size+=value.byteLength;if(size>limit)throw Error('큰 PDF는 새 창에서 열어 주세요.');chunks.push(value);}}
    catch(error){await reader.cancel().catch(()=>{});throw error;}
    if(signal.aborted)throw Error('PDF 불러오기를 취소했습니다.');
    const blob=new Blob(chunks,{type:'application/pdf'});
    if(await blob.slice(0,5).text()!=='%PDF-')throw Error('PDF 형식의 파일인지 확인해 주세요.');
    return blob;
  }
  function transfer(host){
    let active=null,disposed=false;
    function cancel(){const old=active;active=null;old?.controller.abort();}
    async function upload(id,file){
      cancel();const block=host.get(id);if(disposed||!block||!host.editable()||!file)return false;
      const operation={id,signature:signature(block),controller:new AbortController()};active=operation;host.status?.({busy:true,error:''});
      try{
        const mime=String(file.type||''),name=String(file.name||'');
        const extensions={image:/\.(png|jpe?g|gif|webp|avif|bmp|svg|ico)$/i,video:/\.(mp4|webm|ogg|mov|m4v)$/i,audio:/\.(mp3|wav|ogg|m4a|aac|flac|webm)$/i,pdf:/\.pdf$/i};
        if(extensions[block.type]&&!(mime.startsWith(block.type+'/')||block.type==='pdf'&&mime==='application/pdf'||!mime&&extensions[block.type].test(name)))throw Error(`${labels[block.type]} 파일을 선택해 주세요.`);
        const result=await host.upload(file,operation.controller.signal);
        if(disposed||active!==operation||operation.controller.signal.aborted)return false;
        const current=host.get(id);if(!host.editable()||!current||signature(current)!==operation.signature)throw Error('자료를 넣을 블록이 변경되었습니다. 다시 선택해 주세요.');
        if(!result?.id)throw Error('업로드 결과를 확인할 수 없습니다.');
        if(!host.apply(id,attachment(current,{file_id:result.id,name:result.original_name||result.name||file.name}),operation.signature))throw Error('본문의 저장 상태를 확인해 주세요.');
        return true;
      }catch(error){if(active===operation&&!disposed&&!operation.controller.signal.aborted)host.status?.({busy:false,error:error.message||'파일 업로드에 실패했습니다.'});return false;}
      finally{if(active===operation){active=null;host.status?.({busy:false});}}
    }
    return {upload,cancel,dispose(){disposed=true;cancel();}};
  }
  function create(root,host){
    const panel=document.createElement('section');panel.className='item-resource-picker';panel.hidden=true;panel.setAttribute('role','dialog');root.append(panel);
    let current=null,lastFile=null,busy=false,errorMessage='',disposed=false;
    const job=transfer({...host,status});
    const previews=new Map();
    function refreshPdfs(){
      const frames=new Map([...root.querySelectorAll('[data-item-pdf]')].map(frame=>[frame.dataset.itemPdf,frame]));
      for(const [id,entry] of previews)if(!frames.has(id)||signature(host.get(id))!==entry.signature){entry.controller.abort();if(entry.url)URL.revokeObjectURL(entry.url);previews.delete(id);}
      for(const [id,frame] of frames){
        const block=host.get(id);if(!block)continue;
        const message=root.querySelector(`[data-item-pdf-status="${CSS.escape(id)}"]`);
        const display=entry=>{if(entry.url){if(frame.getAttribute('src')!==entry.url)frame.src=entry.url;frame.hidden=false;message.hidden=true;}else if(entry.error){message.textContent=entry.error;message.hidden=false;}};
        const existing=previews.get(id);if(existing){display(existing);continue;}
        const entry={signature:signature(block),controller:new AbortController(),url:'',error:''};previews.set(id,entry);
        const source=block.props?.file_id?host.fileUrl(block.props.file_id):url(block.props?.url);
        void pdfPreview(source,entry.controller.signal).then(blob=>{
          if(disposed||previews.get(id)!==entry||entry.controller.signal.aborted)return;
          entry.url=URL.createObjectURL(blob);refreshPdfs();
        }).catch(error=>{if(!disposed&&previews.get(id)===entry&&!entry.controller.signal.aborted){entry.error=error.message;refreshPdfs();}});
      }
    }
    function status(value){
      if(!current||disposed)return;busy=value.busy;
      if(value.error!==undefined)errorMessage=value.error;
      panel.querySelectorAll('input,[data-resource-tab],[data-resource-insert]').forEach(el=>el.disabled=busy);
      panel.querySelector('[role=status]').textContent=busy?'파일 업로드 중…':errorMessage;
      const retry=panel.querySelector('[data-resource-retry]');retry.hidden=busy||!errorMessage||!lastFile;
      retry.disabled=busy;
    }
    function close(focus=true){
      const old=current;current=null;lastFile=null;busy=false;errorMessage='';job.cancel();panel.hidden=true;panel.replaceChildren();
      if(focus&&old)root.querySelector(`[data-item-resource="${CSS.escape(old.id)}"]`)?.focus({preventScroll:true});
    }
    function open(id){
      const block=host.get(id);if(!host.editable()||!block||!types.has(block.type)||disposed)return;
      close(false);current={id,signature:signature(block)};
      const label=labels[block.type],canUpload=Object.hasOwn(accepts,block.type);
      panel.setAttribute('aria-label',`${label} 추가 또는 바꾸기`);
      panel.innerHTML=`<div role="tablist" aria-label="자료 넣는 방법">${canUpload?'<button type="button" role="tab" data-resource-tab="upload" aria-selected="true">업로드</button>':''}<button type="button" role="tab" data-resource-tab="link" aria-selected="${!canUpload}">링크</button></div><div data-resource-upload ${canUpload?'':'hidden'}><label class="item-resource-file-label">파일 선택<input type="file" aria-label="${label} 파일" accept="${accepts[block.type]||''}"></label><p>파일을 선택하거나 여기에 붙여넣으세요.</p></div><form data-resource-link ${canUpload?'hidden':''}><input type="url" aria-label="자료 링크" placeholder="https://" value="${host.escape(block.props?.url||'')}"><button type="submit" data-resource-insert>링크 삽입</button></form><p role="status" aria-live="polite"></p><div class="item-resource-footer"><button type="button" data-resource-retry hidden>다시 업로드</button><button type="button" data-resource-cancel>취소</button></div>`;
      panel.hidden=false;place();panel.querySelector(canUpload?'input[type=file]':'input[type=url]')?.focus({preventScroll:true});
    }
    function place(){
      const anchor=current&&root.querySelector(`[data-item-resource="${CSS.escape(current.id)}"]`);if(!anchor)return;
      host.place(panel,{get isConnected(){return anchor.isConnected;},getBoundingClientRect(){
        const rect=anchor.getBoundingClientRect(),width=panel.getBoundingClientRect().width;
        return {left:rect.left+(rect.width-width)/2,right:rect.right,top:rect.top,bottom:rect.bottom};
      }});
    }
    function refresh(){
      refreshPdfs();
      if(!current)return;const block=host.get(current.id);
      if(!host.editable()||!block||signature(block)!==current.signature){close(false);return;}
      place();
    }
    async function upload(file){if(!current||busy||!file)return;lastFile=file;const id=current.id;if(await job.upload(id,file))close();}
    const click=event=>{
      const button=event.target.closest('[data-item-resource]');if(button){event.preventDefault();open(button.dataset.itemResource);}
    };
    const outside=event=>{if(current&&!panel.contains(event.target)&&!event.target.closest('[data-item-resource]'))close(false);};
    panel.addEventListener('click',event=>{
      if(event.target.closest('[data-resource-cancel]')){event.preventDefault();close();return;}
      if(event.target.closest('[data-resource-retry]')){void upload(lastFile);return;}
      const tab=event.target.closest('[data-resource-tab]');if(!tab||busy)return;
      const uploadTab=tab.dataset.resourceTab==='upload';panel.querySelector('[data-resource-upload]').hidden=!uploadTab;panel.querySelector('[data-resource-link]').hidden=uploadTab;
      panel.querySelectorAll('[data-resource-tab]').forEach(el=>el.setAttribute('aria-selected',String(el===tab)));
      panel.querySelector(uploadTab?'input[type=file]':'input[type=url]')?.focus();
    });
    panel.addEventListener('change',event=>{if(event.target.type==='file')void upload(event.target.files?.[0]);});
    panel.addEventListener('submit',event=>{
      event.preventDefault();event.stopPropagation();if(!current||busy||!host.editable())return;
      try{const block=host.get(current.id);if(!block||signature(block)!==current.signature)throw Error('블록이 변경되었습니다.');
        const value=attachment(block,{url:panel.querySelector('input[type=url]').value});
        if(!host.apply(current.id,value,current.signature))throw Error('본문의 저장 상태를 확인해 주세요.');close();
      }catch(error){status({busy:false,error:error.message});}
    });
    panel.addEventListener('paste',event=>{
      if(panel.querySelector('[data-resource-upload]').hidden||!current||busy)return;
      const file=event.clipboardData?.files?.[0]||[...(event.clipboardData?.items||[])].find(item=>item.kind==='file')?.getAsFile();
      if(file){event.preventDefault();event.stopPropagation();void upload(file);}
    });
    panel.addEventListener('keydown',event=>{
      event.stopPropagation();if(event.key==='Escape'){event.preventDefault();close();return;}
      if(event.target.matches('[data-resource-tab]')&&['ArrowLeft','ArrowRight','Home','End'].includes(event.key)){
        event.preventDefault();const tabs=[...panel.querySelectorAll('[data-resource-tab]')],index=tabs.indexOf(event.target);
        const next=event.key==='Home'?0:event.key==='End'?tabs.length-1:(index+(event.key==='ArrowLeft'?-1:1)+tabs.length)%tabs.length;tabs[next]?.click();tabs[next]?.focus();return;
      }
      if(event.key==='Tab'){const elements=[...panel.querySelectorAll('input,button')].filter(el=>!el.disabled&&el.getClientRects().length),index=elements.indexOf(document.activeElement);if(event.shiftKey&&index<=0){event.preventDefault();elements.at(-1)?.focus();}else if(!event.shiftKey&&index===elements.length-1){event.preventDefault();elements[0]?.focus();}}
    });
    root.addEventListener('click',click);document.addEventListener('pointerdown',outside);
    return {open,refresh,render:block=>render(block,{...host,editable:host.editable()}),dispose(){disposed=true;close(false);job.dispose();for(const entry of previews.values()){entry.controller.abort();if(entry.url)URL.revokeObjectURL(entry.url);}previews.clear();root.removeEventListener('click',click);document.removeEventListener('pointerdown',outside);panel.remove();}};
  }
  return {types,url,signature,attachment,render,transfer,pdfPreview,create};
});

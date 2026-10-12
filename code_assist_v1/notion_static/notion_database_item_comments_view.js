(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.NotionDatabaseItemCommentsView=api;})(typeof globalThis==='object'?globalThis:this,function(){
  'use strict';
  const escape=text=>String(text??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  function create(root,host){
    let model=null,disposed=false,composing=false,menu='',showResolved=false,signature='',mentionOpen=false,mentionGeneration=0,uploading=false,uploadAbort=null,uploadError='';
    root.innerHTML='<div class="item-comments-heading"><span>댓글</span><button type="button" data-comment-action="resolved" aria-pressed="false">완료된 댓글</button></div><div class="item-comments-list"></div><div class="item-comment-context" hidden><span></span><button type="button" data-comment-action="cancel">취소</button></div><div class="item-comment-composer"><span class="item-comment-avatar" aria-hidden="true"></span><div class="item-comment-compose-body"><textarea rows="1" maxlength="20000" disabled aria-label="항목 댓글" placeholder="댓글 추가…"></textarea><div class="item-comment-tools"><button type="button" data-comment-action="mention" disabled aria-label="사용자 언급">@</button><button type="button" data-comment-action="send" disabled aria-label="댓글 보내기"><svg viewBox="0 0 20 20" width="18" height="18" aria-hidden="true"><path d="M10 15V5M5.5 9.5 10 5l4.5 4.5" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg></button></div></div></div><div class="item-comment-mentions" role="group" aria-label="언급할 사용자" hidden></div><div class="item-comment-notice"><span role="status" aria-live="polite"></span><button type="button" data-comment-action="retry" hidden>저장 다시 확인</button><button type="button" data-comment-action="discard" hidden>보관한 요청과 초안 지우기</button><button type="button" data-comment-action="refresh" hidden>댓글 새로고침</button></div>';
    const tools=root.querySelector('.item-comment-tools'),insertTools=document.createElement('span');
    insertTools.className='item-comment-insert-tools';
    tools.insertBefore(insertTools,tools.querySelector('[data-comment-action="mention"]'));
    insertTools.append(tools.querySelector('[data-comment-action="mention"]'));
    insertTools.insertAdjacentHTML('beforeend','<button type="button" data-comment-action="attach" aria-label="댓글 파일 첨부" hidden><svg viewBox="0 0 20 20" width="18" height="18" aria-hidden="true"><path d="m7 11 5-5a3 3 0 0 1 4.2 4.2l-6.9 6.9a5 5 0 0 1-7.1-7.1L9 3.2" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg></button><input type="file" data-comment-file-input hidden>');
    const fileInput=root.querySelector('[data-comment-file-input]');
    const list=root.querySelector('.item-comments-list'),input=root.querySelector('textarea'),context=root.querySelector('.item-comment-context'),status=root.querySelector('[role="status"]'),mentions=root.querySelector('.item-comment-mentions');
    const button=action=>root.querySelector(`[data-comment-action="${action}"]`);
    const allowed=()=>!disposed&&model?.ready&&model.canComment&&!model.busy&&!model.pending&&!uploading;
    const person=id=>host.person?.(id)||id;
    const stamp=value=>{const n=Number(value),date=new Date(n<1e12?n*1000:n);return Number.isNaN(date.getTime())?'':date.toLocaleString('ko-KR',{month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit'});};
    const fileLink=/\[([^\]\n]{1,200})\]\((\/api\/notion\/workspaces\/[A-Za-z0-9_-]+\/databases\/[A-Za-z0-9_-]+\/files\/[A-Za-z0-9_-]+)\)/g;
    function contentHtml(value){
      const raw=String(value||'');let output='',cursor=0;
      for(const match of raw.matchAll(fileLink)){
        output+=escape(raw.slice(cursor,match.index));
        output+=`<a href="${escape(match[2])}" target="_blank" rel="noopener">${escape(match[1])}</a>`;
        cursor=match.index+match[0].length;
      }
      return output+escape(raw.slice(cursor));
    }
    function resize(){input.style.height='auto';input.style.height=`${Math.min(240,input.scrollHeight)}px`;}
    function notice(text){status.textContent=text;}
    function paintList(){
      const sig=JSON.stringify([model.items,model.canComment,model.busy,model.pending,showResolved,menu]);if(signature===sig)return;signature=sig;
      const ids=new Set(model.items.map(item=>item.id));
      const roots=model.items.filter(item=>(!item.parent_comment_id||!ids.has(item.parent_comment_id))&&Boolean(item.resolved)===showResolved);
      const comment=item=>{
        const author=person(item.author_subject_id),own=host.canEdit(item),disabled=!allowed()?'disabled':'',isRoot=!item.parent_comment_id;
        const controls=menu===item.id?`<div class="item-comment-menu" role="group" aria-label="댓글 작업">${isRoot?`<button type="button" data-comment-action="reply" ${disabled}>답글</button><button type="button" data-comment-action="resolve" ${disabled}>${item.resolved?'다시 열기':'완료'}</button>`:''}<button type="button" data-comment-action="react" ${disabled}>👍 반응</button>${own?`<button type="button" data-comment-action="edit" ${disabled}>수정</button><button type="button" data-comment-action="delete" ${disabled}>삭제</button>`:''}</div>`:'';
        const reactions=Object.entries(item.reactions||{}).filter(([,people])=>Array.isArray(people)&&people.length).map(([emoji,people])=>`<button type="button" data-comment-action="reaction" data-emoji="${escape(emoji)}" aria-pressed="${people.includes(model.userId)}" ${disabled}>${escape(emoji)} ${people.length}</button>`).join('');
        return `<article class="item-comment${isRoot?'':' reply'}" data-item-comment="${escape(item.id)}"><span class="item-comment-avatar" aria-hidden="true">${escape([...author][0]||'?')}</span><div class="item-comment-text"><div class="item-comment-byline"><strong>${escape(author)}</strong><time>${escape(stamp(item.created_at))}</time>${model.canComment?`<button type="button" data-comment-action="menu" aria-label="${escape(author)} 댓글 작업" aria-expanded="${menu===item.id}">···</button>`:''}</div><p>${contentHtml(item.content)}</p>${controls}<div class="item-comment-reactions">${reactions}</div></div></article>`;
      };
      list.innerHTML=roots.map(item=>comment(item)+model.items.filter(reply=>reply.parent_comment_id===item.id).map(comment).join('')).join('');
      list.hidden=!roots.length;
    }
    function render(value){
      if(disposed||!value)return;model=value;
      root.setAttribute('aria-busy',String(model.busy));paintList();
      if(!composing&&input.value!==model.draft.content)input.value=model.draft.content;
      input.disabled=!model.ready||!model.canComment||model.busy||model.pending;
      input.placeholder=model.canComment?'댓글 추가…':'댓글을 작성할 권한이 없습니다';
      button('send').disabled=!allowed()||composing||!model.draft.content.trim();button('mention').disabled=!allowed()||host.allowMentions===false;button('mention').hidden=host.allowMentions===false;
      button('attach').hidden=typeof host.uploadFile!=='function'||!['edit_content','edit','full_access'].includes(model.role);
      button('attach').disabled=!allowed()||button('attach').hidden;
      button('resolved').setAttribute('aria-pressed',String(showResolved));button('resolved').textContent=showResolved?'진행 중인 댓글':'완료된 댓글';
      context.hidden=!model.draft.parent&&!model.draft.editing;context.querySelector('span').textContent=model.draft.editing?'댓글 수정 중':'답글 작성 중';button('cancel').disabled=model.busy||model.pending;
      root.querySelector('.item-comment-composer > .item-comment-avatar').textContent=[...person(model.userId)][0]||'?';
      notice(uploadError||model.error||(uploading?'댓글 파일을 올리는 중…':model.busy?'댓글 저장 확인 중…':model.pending?'보관한 댓글 요청을 확인해 주세요.':model.savingDraft?'초안 보관 중…':model.draft.content?'초안이 이 기기에 보관됩니다.':''));
      button('retry').hidden=!model.pending;button('retry').disabled=model.busy||!model.canComment;
      button('discard').hidden=!model.pending&&(model.ready||!model.error);button('discard').disabled=model.busy;
      button('refresh').hidden=!model.error||model.pending;button('refresh').disabled=model.busy;
      if(!allowed()&&mentionOpen){mentionOpen=false;mentionGeneration++;mentions.hidden=true;}resize();
    }
    function change(){if(host.draft({content:input.value})){uploadError='';render(host.snapshot());}}
    async function click(event){
      const target=event.target.closest('[data-comment-action]');if(!target||target.disabled)return;event.stopPropagation();event.preventDefault();
      const action=target.dataset.commentAction,id=target.closest('[data-item-comment]')?.dataset.itemComment,item=model?.items.find(item=>item.id===id);
      if(action==='resolved'){showResolved=!showResolved;menu='';render(model);return;}
      if(action==='discard'){
        if(target.dataset.confirm!=='true'){target.dataset.confirm='true';target.textContent='이 기기의 요청과 초안 지우기 확인';notice('이미 저장된 댓글은 유지됩니다. 보관한 요청과 초안만 지웁니다.');return;}
        await host.discard();return;
      }
      if(action==='refresh'){await host.refresh();return;}if(action==='retry'){await host.retry();return;}
      if(action==='menu'){menu=menu===id?'':id;paintList();if(menu)list.querySelector(`[data-item-comment="${CSS.escape(id)}"] .item-comment-menu button`)?.focus();return;}
      if(!allowed())return;
      if(action==='attach'){fileInput.click();return;}
      if(action==='send'){if(!composing)await host.submit();return;}
      if(action==='cancel'){host.draft({content:'',editing:'',parent:''});render(host.snapshot());input.focus();return;}
      if(['reply','edit'].includes(action)){
        if(model.draft.content){notice('작성 중인 댓글을 보내거나 취소한 뒤 선택해 주세요.');return;}
        host.draft(action==='edit'?{content:item.content,editing:id,parent:''}:{content:'',parent:id,editing:''});menu='';render(host.snapshot());input.focus();return;
      }
      if(action==='mention'){
        if(host.allowMentions===false)return;
        mentionOpen=!mentionOpen;mentions.hidden=!mentionOpen;
        const generation=++mentionGeneration;
        if(mentionOpen){
          mentions.textContent='언급할 사용자를 확인하는 중…';
          try{
            const people=await host.people?.()||[];
            if(disposed||!mentionOpen||generation!==mentionGeneration||!allowed())return;
            mentions.innerHTML=people.map(person=>`<button type="button" data-comment-action="mention-person" data-person="${escape(person.id)}">${escape(person.display_name||person.id)} <small>@${escape(person.id)}</small></button>`).join('')||'<span>언급할 사용자가 없습니다.</span>';
            mentions.querySelector('button')?.focus();
          }catch(error){
            if(!disposed&&mentionOpen&&generation===mentionGeneration)
              mentions.textContent='사용자 권한을 확인하지 못했습니다. 다시 시도해 주세요.';
          }
        }return;
      }
      if(action==='mention-person'){
        const prefix=input.value&&!/\s$/.test(input.value)?' ':'';input.setRangeText(`${prefix}@${target.dataset.person} `,input.selectionStart,input.selectionEnd,'end');change();mentionOpen=false;mentionGeneration++;mentions.hidden=true;input.focus();return;
      }
      if(action==='delete'){
        if(target.dataset.confirm!=='true'){target.dataset.confirm='true';target.textContent='삭제 확인';return;}
        await host.mutate('delete',id);
      }else if(action==='resolve')await host.mutate('resolve',id,!item.resolved);
      else if(action==='react'||action==='reaction')await host.mutate('react',id,target.dataset.emoji||'👍');
      menu='';render(host.snapshot());
    }
    function key(event){
      // Never let item/body shortcuts handle comment typing, history or composition.
      event.stopPropagation();if(event.isComposing||event.keyCode===229||composing)return;
      if(event.key==='Escape'){
        event.preventDefault();if(mentionOpen){mentionOpen=false;mentionGeneration++;mentions.hidden=true;input.focus();}
        else if(menu){menu='';paintList();input.focus();}else input.blur();
      }else if(event.target===input&&event.key==='Enter'&&!event.shiftKey&&(event.ctrlKey||event.metaKey)){event.preventDefault();if(allowed())void host.submit();}
    }
    function compositionStart(event){event.stopPropagation();composing=true;host.composing(true);button('send').disabled=true;}
    function compositionEnd(event){event.stopPropagation();composing=false;host.composing(false);change();}
    async function selectedFile(event){
      const file=event.target.files?.[0];event.target.value='';
      if(!file||!allowed()||typeof host.uploadFile!=='function')return;
      const controller=uploadAbort=new AbortController();uploading=true;uploadError='';render(model);
      try{
        const uploaded=await host.uploadFile(file,controller.signal);
        if(disposed||controller.signal.aborted)return;
        const url=String(uploaded?.url||'');
        if(!/^\/api\/notion\/workspaces\/[A-Za-z0-9_-]+\/databases\/[A-Za-z0-9_-]+\/files\/[A-Za-z0-9_-]+$/.test(url))throw Error('댓글 파일 주소가 표와 일치하지 않습니다.');
        const name=String(uploaded?.original_name||file.name||'첨부 파일').replace(/[\[\]\r\n]/g,'_').slice(0,200)||'첨부 파일';
        input.value+=`${input.value&&!/\s$/.test(input.value)?'\n':''}[${name}](${url})`;
        change();input.focus();
      }catch(error){if(!disposed&&!controller.signal.aborted)uploadError=`댓글 파일 첨부 실패: ${error.message}`;}
      finally{if(uploadAbort===controller){uploadAbort=null;uploading=false;if(!disposed)render(model);}}
    }
    root.addEventListener('click',click);root.addEventListener('keydown',key);input.addEventListener('input',change);
    root.addEventListener('compositionstart',compositionStart);root.addEventListener('compositionend',compositionEnd);
    fileInput.addEventListener('change',selectedFile);
    return {render,focus:()=>input.focus(),dispose:()=>{disposed=true;mentionGeneration++;uploadAbort?.abort();host.composing(false);root.removeEventListener('click',click);root.removeEventListener('keydown',key);input.removeEventListener('input',change);fileInput.removeEventListener('change',selectedFile);root.removeEventListener('compositionstart',compositionStart);root.removeEventListener('compositionend',compositionEnd);root.replaceChildren();}};
  }
  return {create};
});

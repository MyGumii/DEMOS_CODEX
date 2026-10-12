(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.NotionDatabaseItemAppearanceView=api;})(typeof globalThis==='object'?globalThis:this,function(){
  'use strict';
  const covers=['linear-gradient(120deg,#dfe9f3 0%,#fff 100%)','linear-gradient(120deg,#f6d365 0%,#fda085 100%)','linear-gradient(120deg,#84fab0 0%,#8fd3f4 100%)','linear-gradient(120deg,#a18cd1 0%,#fbc2eb 100%)','linear-gradient(120deg,#fccb90 0%,#d57eeb 100%)','linear-gradient(120deg,#e0c3fc 0%,#8ec5fc 100%)'];
  function create(root,notice,host){
    let model=null,crop=null,drag=null,disposed=false,renderedIcon=null;
    root.innerHTML='<div class="item-appearance-cover" hidden><img alt="항목 커버" draggable="false"><div class="item-cover-actions"><button type="button" data-item-appearance="cover">커버 변경</button><button type="button" data-item-appearance="position">위치 변경</button><button type="button" data-item-appearance="remove-cover">커버 제거</button></div></div><div class="item-cover-position" hidden><label>커버 위치 <input type="range" min="0" max="100" step="1" aria-label="커버 세로 위치"></label><button type="button" data-item-appearance="save-position">저장</button><button type="button" data-item-appearance="cancel-position">취소</button></div><div class="item-appearance-heading"><button type="button" class="item-appearance-icon" data-item-appearance="icon" aria-label="아이콘 추가"></button><button type="button" class="item-add-cover" data-item-appearance="cover">커버 추가</button></div>';
    notice.innerHTML='<span role="status"></span><button type="button" data-item-appearance="retry">다시 저장</button><button type="button" data-item-appearance="discard">보관한 변경 지우기</button>';
    const cover=root.querySelector('.item-appearance-cover'),image=cover.querySelector('img'),heading=root.querySelector('.item-appearance-heading'),icon=root.querySelector('.item-appearance-icon'),position=root.querySelector('.item-cover-position'),slider=position.querySelector('input');
    const allowed=()=>Boolean(!disposed&&model?.ready&&model.editable&&!model.busy&&!model.pending);
    function preview(){image.style.objectPosition=`center ${(crop??model?.appearance?.cover_position??.5)*100}%`;if(crop!==null)slider.value=String(Math.round(crop*100));}
    function cancel(){const pointer=drag?.id;crop=null;drag=null;if(pointer!=null&&cover.hasPointerCapture(pointer))cover.releasePointerCapture(pointer);position.hidden=true;cover.classList.remove('repositioning');preview();}
    async function click(event){
      const action=event.target.closest('[data-item-appearance]')?.dataset.itemAppearance;if(!action)return;
      event.preventDefault();event.stopPropagation();
      if(action==='retry'){await host.retry();return;}if(action==='discard'){await host.discard();return;}
      if(action==='cancel-position'){cancel();return;}if(!allowed())return;
      if(action==='icon')await host.icon(event.target.closest('button'));
      else if(action==='cover')await host.cover(event.target.closest('button'));
      else if(action==='remove-cover')await host.save({cover:''});
      else if(action==='position'){crop=model.appearance.cover_position;position.hidden=false;cover.classList.add('repositioning');preview();slider.focus();}
      else if(action==='save-position'){if(await host.save({cover_position:crop}))cancel();}
    }
    const onKey=event=>{if(crop!==null&&event.key==='Escape'){event.preventDefault();event.stopPropagation();cancel();root.querySelector('[data-item-appearance="position"]').focus();}};
    const onInput=()=>{crop=Number(slider.value)/100;preview();};
    const down=event=>{if(crop===null||!allowed()||event.button!==0||event.target.closest('button'))return;event.preventDefault();event.stopPropagation();drag={id:event.pointerId,y:event.clientY,position:crop};cover.setPointerCapture(event.pointerId);};
    const move=event=>{if(!drag||event.pointerId!==drag.id)return;event.preventDefault();const rect=cover.getBoundingClientRect(),scale=Math.max(rect.width/(image.naturalWidth||rect.width),rect.height/(image.naturalHeight||rect.height));
      const overflow=Math.max(1,(image.naturalHeight||rect.height)*scale-rect.height);crop=Math.max(0,Math.min(1,drag.position-(event.clientY-drag.y)/overflow));preview();};
    const up=event=>{if(!drag||event.pointerId!==drag.id)return;drag=null;if(cover.hasPointerCapture(event.pointerId))cover.releasePointerCapture(event.pointerId);};
    root.addEventListener('click',click);notice.addEventListener('click',click);root.addEventListener('keydown',onKey);slider.addEventListener('input',onInput);
    cover.addEventListener('pointerdown',down);cover.addEventListener('pointermove',move);cover.addEventListener('pointerup',up);cover.addEventListener('pointercancel',up);cover.addEventListener('lostpointercapture',()=>drag=null);
    function render(value){
      if(disposed||!value)return;const prior=model;model=value;
      if(crop!==null&&(!model.editable||model.pending||prior?.appearance?.cover!==model.appearance.cover))cancel();
      const a=model.appearance,editable=allowed(),gradient=/^linear-gradient\([#a-zA-Z0-9%., ()-]+\)$/.test(a.cover),url=gradient?'':host.url(a.cover);
      cover.hidden=!a.cover;cover.style.backgroundImage=gradient?a.cover:'';image.hidden=!url;if(url&&image.getAttribute('src')!==url)image.src=url;if(!url)image.removeAttribute('src');
      root.classList.toggle('has-icon',Boolean(a.icon));root.classList.toggle('has-cover',Boolean(a.cover));root.parentElement.classList.toggle('has-item-cover',Boolean(a.cover));
      icon.hidden=!a.icon&&!model.editable;icon.classList.toggle('empty',!a.icon);icon.setAttribute('aria-label',a.icon?'항목 아이콘 변경':'아이콘 추가');
      if(renderedIcon!==a.icon){renderedIcon=a.icon;icon.innerHTML=a.icon?host.iconHtml(a.icon):'아이콘 추가';}
      heading.querySelector('.item-add-cover').hidden=Boolean(a.cover)||!model.editable;
      cover.querySelector('.item-cover-actions').hidden=!model.editable;
      cover.querySelector('[data-item-appearance="position"]').hidden=!url;
      root.querySelectorAll('button,input').forEach(el=>el.disabled=!editable);preview();
      notice.hidden=!model.busy&&!model.pending&&!model.error;notice.querySelector('[role="status"]').textContent=model.error||(model.busy?'모양 저장 중…':model.pending?'보관한 아이콘·커버 변경을 확인해 주세요.':'');
      for(const button of notice.querySelectorAll('button')){button.hidden=!model.pending&&!model.error;button.disabled=model.busy;}
    }
    return {render,dispose:()=>{disposed=true;cancel();root.removeEventListener('click',click);root.removeEventListener('keydown',onKey);notice.removeEventListener('click',click);notice.replaceChildren();notice.hidden=true;root.parentElement?.classList.remove('has-item-cover');root.replaceChildren();}};
  }
  return {create,covers};
});

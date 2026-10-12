(function(root,factory){ const api=factory();if(typeof module==="object"&&module.exports)module.exports=api;root.NotionCreationChooser=api; })(typeof window!=="undefined"?window:globalThis,function(){
  "use strict";
  const items=Object.freeze([
    {id:"page",name:"빈 페이지",title:"제목 없음",icon:"page",group:"blank",search:"page empty 문서",description:"빈 문서에서 시작하세요."},
    {id:"database",name:"빈 데이터베이스",title:"새 데이터베이스",icon:"table",group:"blank",search:"database table 표",description:"이름 속성이 있는 빈 표를 만듭니다."},
    {id:"linked_database",name:"기존 데이터 소스 연결",title:"연결된 데이터베이스",icon:"table",group:"blank",search:"database link source 연결 원본",description:"기존 데이터베이스의 보기를 새 페이지에 연결합니다."},
    {id:"task_tracker",name:"작업 트래커",title:"작업 트래커",icon:"check",group:"template",search:"tasks todo 작업 목록 할 일 프로젝트 보드",description:"작업의 상태와 기한을 표와 보드로 관리하세요."},
    {id:"meeting_notes",name:"회의록",title:"회의록",icon:"page",group:"template",search:"meeting notes 회의 노트 안건",description:"회의 안건, 논의 내용과 다음 할 일을 정리하세요."},
  ]);
  const escape=value=>String(value??"").replace(/[&<>"']/g,char=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[char]));
  function available(ids){return items.filter(item=>ids.includes(item.id));}
  function filter(ids,query){const q=String(query||"").trim().toLocaleLowerCase();return available(ids).filter(item=>`${item.name} ${item.description} ${item.search}`.toLocaleLowerCase().includes(q));}
  function mount(root,options){
    const choices=available(options.templates), destinations=options.destinations.map(item=>({...item}));
    if(!choices.length||!destinations.length)throw Error("생성할 수 있는 위치가 없습니다.");
    const selected=destinations.some(item=>item.id===options.selected)?options.selected:destinations[0].id;
    let submitted=false,disposed=false;
    root.innerHTML=`<div class="creation-chooser-toolbar"><label class="creation-destination">추가 대상 <select aria-label="페이지 생성 위치">${destinations.map(d=>`<option value="${escape(d.id)}"${d.id===selected?' selected':''}>${escape(d.name)}</option>`).join("")}</select></label><input class="creation-search" type="search" aria-label="생성 항목 검색" placeholder="검색" autocomplete="off"></div><div class="creation-chooser-content"><div data-creation-results></div><p class="creation-empty" role="status" hidden>일치하는 생성 항목이 없습니다.</p></div>`;
    const search=root.querySelector('.creation-search'),select=root.querySelector('select'),results=root.querySelector('[data-creation-results]'),empty=root.querySelector('.creation-empty');
    const current=()=>!disposed&&!submitted&&root.isConnected&&options.isCurrent();
    const preview=item=>item.id==='meeting_notes'
      ? '<div class="creation-preview creation-note-preview" aria-hidden="true"><strong>회의 안건</strong><i></i><strong>논의 내용</strong><i></i><strong>다음 할 일</strong><span>☐</span></div>'
      : '<div class="creation-preview" aria-hidden="true"><div class="creation-preview-head"><span>작업 이름</span><span>상태</span><span>기한</span></div><div><i></i><b>시작 전</b><i></i></div><div><i></i><b>진행 중</b><i></i></div><div><i></i><b>완료</b><i></i></div></div>';
    function render(){
      const matches=filter(choices.map(item=>item.id),search.value);
      results.innerHTML=['blank','template'].map(group=>{
        const grouped=matches.filter(item=>item.group===group);if(!grouped.length)return '';
        return `<section class="creation-section">${group==='template'?'<h3>템플릿</h3>':''}<div class="creation-grid">${grouped.map(item=>`<button type="button" class="creation-card creation-card-${group}" data-create-template="${item.id}" aria-label="${escape(item.name)}"><span class="creation-card-icon" aria-hidden="true">${options.icon(item.icon)}</span><strong>${item.name}</strong>${group==='template'?`<p>${item.description}</p>${preview(item)}`:''}</button>`).join('')}</div></section>`;
      }).join('');empty.hidden=matches.length!==0;
      results.querySelectorAll('[data-create-template]').forEach(button=>button.addEventListener('click',()=>{
        if(!current())return;
        const item=choices.find(item=>item.id===button.dataset.createTemplate),destination=destinations.find(item=>item.id===select.value);
        if(!item||!destination||!options.canCreate(destination.id))return;
        submitted=true;
        options.submit({template:item.id,title:item.title,teamspaceId:destination.id});
      }));
    }
    search.addEventListener('input',()=>{if(current())render();});
    search.addEventListener('keydown',event=>{
      if(event.isComposing||event.keyCode===229)return;
      if(event.key==='ArrowDown'){event.preventDefault();results.querySelector('button')?.focus();}
    });
    render();search.focus({preventScroll:true});
    return {dispose(){disposed=true;},selected:()=>select.value};
  }
  return {available,filter,mount};
});

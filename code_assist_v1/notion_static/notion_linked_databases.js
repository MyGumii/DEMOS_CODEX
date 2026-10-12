(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.NotionLinkedDatabases=api;})(typeof globalThis==='object'?globalThis:this,function(){
  'use strict';
  const TYPES={table:'표',board:'보드',list:'리스트',gallery:'갤러리',calendar:'캘린더',timeline:'타임라인'};
  const KEYS=['filters','filter_group','sorts','group_property','group_sort','group_order','month','timeline_anchor','timeline_scale','timeline_date_property','board_group_property','calendar_date_property','property_visibility','property_order','show_source_title','show_vertical_lines','show_page_icons','wrap_all_content'];
  const TABLE_OPTIONS=[['show_source_title','데이터 소스 제목 표시'],['show_vertical_lines','세로선 표시'],['show_page_icons','페이지 아이콘 표시'],['wrap_all_content','모든 콘텐츠 줄바꿈']];
  const GROUP_SORTS=[['manual','수동'],['asc','오름차순'],['desc','내림차순'],['count_asc','항목 적은 순'],['count_desc','항목 많은 순']];
  const reference=block=>Object.prototype.hasOwnProperty.call(block?.props||{},'linked_database');
  const clone=value=>structuredClone(value);
  const escape=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  function rowCreateHtml(data,blockId,canCreateTemplate=false,icon=null){
    const templates=(data.database?.templates||[]).filter(item=>item.state==='active');
    const defaultId=data.database?.default_template?.template_id||'';
    const defaultItem=templates.find(item=>item.id===defaultId);
    const unavailable=Boolean(defaultId&&(!defaultItem||defaultItem.body_unavailable||defaultItem.default_unavailable));
    const menuId=`linked-row-templates-${blockId}`;
    return `<div class="database-create-cluster linked-create-cluster">
      <button type="button" data-linked-row-create ${unavailable?'data-default-unavailable="true" title="기본 템플릿에 접근할 수 없습니다. 비어 있음을 선택하세요."':''} disabled>새로 만들기</button>
      ${unavailable?'<button type="button" data-linked-row-create-blank disabled>비어 있음</button>':''}
      ${templates.length||defaultId||canCreateTemplate?`<button type="button" data-linked-template-picker aria-label="항목 템플릿 선택" aria-haspopup="menu" aria-expanded="false" aria-controls="${escape(menuId)}" disabled>${icon?.('down')||'⌄'}</button>
        <div id="${escape(menuId)}" class="database-template-popover independent-template-popover" data-linked-template-menu popover="auto" role="menu" aria-label="항목 템플릿">
          <div class="database-template-header"><strong>템플릿 <span>${escape(data.database?.title||'데이터베이스')}</span></strong><a class="database-template-help" href="https://www.notion.com/ko/help/database-templates" target="_blank" rel="noopener noreferrer" aria-label="템플릿 도움말" title="도움말">${icon?.('help')||'?'}</a></div>
          <p class="independent-template-description">${templates.length||defaultId?'이 데이터베이스에서 사용할 페이지 템플릿을 선택합니다.':'이 데이터베이스에 대한 재사용 가능한 페이지 템플릿을 생성합니다.'}</p>
          <div class="independent-template-options">
            ${templates.length||defaultId?`<button type="button" role="menuitem" data-linked-template-id="">${icon?.('page')||'□'}<span>비어 있음${!defaultId?' · 기본':''}</span></button>`:''}
            ${templates.map(item=>`<div class="database-template-choice"><button type="button" role="menuitem" data-linked-template-id="${escape(item.id)}" ${item.body_unavailable||item.default_unavailable?'disabled title="이 템플릿에 접근할 수 없습니다."':''}>${icon?.('page')||'□'}<span>${escape(item.name)}${defaultId===item.id?' · 기본':''}${item.body_unavailable||item.default_unavailable?' · 접근 불가':''}</span></button>${canCreateTemplate&&!item.body_unavailable&&!item.default_unavailable?`<button type="button" data-linked-template-edit="${escape(item.id)}" aria-label="${escape(item.name)} 템플릿 편집" disabled>${icon?.('more')||'⋯'}</button>`:''}</div>`).join('')}
            ${canCreateTemplate?`<button type="button" role="menuitem" data-linked-template-create disabled>${icon?.('plus')||'＋'}<span>새 템플릿</span></button>`:''}
          </div>
        </div>`:''}
      ${unavailable?'<span class="database-create-hint">기본 템플릿에 접근할 수 없습니다.</span>':''}
    </div>`;
  }
  const id=()=>`linkedview_${crypto.randomUUID().replaceAll('-','')}`;
  const MENU_LAYOUTS=['table','board','timeline','calendar','list','gallery'];
  const FILTER_OPERATORS={contains:'포함',not_contains:'포함하지 않음',equals:'같음',not_equals:'같지 않음',greater_than:'보다 큼',less_than:'보다 작음',empty:'비어 있음',not_empty:'비어 있지 않음',on_or_before:'이전 또는 같음',on_or_after:'이후 또는 같음'};
  const propertyGlyph=(type,icon)=>type==='date'?icon('calendar')
    :type==='relation'?icon('link')
    :type==='files'?icon('attachment')
    :({title:'Aa',text:'Aa',number:'#',checkbox:'☑',url:'↗',select:'▤',status:'◉',multi_select:'▤',person:'♙'})[type]||'◇';
  function sortGroupEntries(entries,mode='manual',order=[]){
    const items=[...entries],positions=new Map((Array.isArray(order)?order:[]).map((label,index)=>[String(label),index]));
    const collator=new Intl.Collator('ko',{numeric:true,sensitivity:'base'});
    const alphabetic=(a,b)=>a[0]==='비어 있음'?1:b[0]==='비어 있음'?-1:collator.compare(String(a[0]),String(b[0]));
    if(mode==='asc')items.sort(alphabetic);
    else if(mode==='desc')items.sort((a,b)=>a[0]==='비어 있음'?1:b[0]==='비어 있음'?-1:-collator.compare(String(a[0]),String(b[0])));
    else if(mode==='count_asc'||mode==='count_desc')items.sort((a,b)=>
      (a[1].length-b[1].length)*(mode==='count_desc'?-1:1)||alphabetic(a,b));
    else if(positions.size)items.sort((a,b)=>
      (positions.get(String(a[0]))??Number.MAX_SAFE_INTEGER)-(positions.get(String(b[0]))??Number.MAX_SAFE_INTEGER));
    return items;
  }
  function groupLabel(property,item){
    const value=item?.[property.id];
    if(property.type==='checkbox')return value?'체크됨':'체크 안 됨';
    if(Array.isArray(value))return value.map(part=>part?.name||part).filter(Boolean).join(', ')||'비어 있음';
    if(value&&typeof value==='object'){
      const start=String(value.start||value.name||''),end=String(value.end||'');
      return start?`${start}${end&&end!==start?` → ${end}`:''}`:'비어 있음';
    }
    return String(value??'')||'비어 있음';
  }
  function groupEntriesForRows(rows,property){
    const groups=new Map();
    if(!property)return [];
    for(const row of rows||[]){
      if(!row||row.archived)continue;
      const label=groupLabel(property,row);
      if(!groups.has(label))groups.set(label,[]);
      groups.get(label).push(row);
    }
    return [...groups.entries()];
  }
  function boardGroupEntriesForRows(rows,properties,config={}){
    const candidates=properties.filter(property=>['status','select','person','checkbox','multi_select'].includes(property.type));
    const property=candidates.find(item=>item.id===config.board_group_property)||candidates[0];
    const groups=new Map();
    const predefined=property?.type==='checkbox'?['체크 안 됨','체크됨']
      :[...(property?.type==='status'?['시작 전','진행 중','완료']:[]),
        ...(Array.isArray(property?.options)?property.options:[])];
    [...new Set(predefined.filter(Boolean))].forEach(label=>groups.set(label,[]));
    for(const row of rows||[]){
      if(!row||row.archived)continue;
      const raw=row[property?.id];
      const keys=property?.type==='checkbox'?[raw?'체크됨':'체크 안 됨']
        :property?.type==='multi_select'?String(raw||'').split(',').map(value=>value.trim()).filter(Boolean)
        :[raw||'상태 없음'];
      for(const label of keys.length?keys:['상태 없음']){
        if(!groups.has(label))groups.set(label,[]);
        groups.get(label).push(row);
      }
    }
    if(!groups.size)groups.set('상태 없음',[]);
    return {property,entries:[...groups.entries()]};
  }
  function movedGroupOrder(entries,mode,order,label,direction){
    const labels=sortGroupEntries(entries,mode,order).map(([name])=>name);
    const index=labels.indexOf(label),target=index+direction;
    if(index<0||target<0||target>=labels.length)return null;
    [labels[index],labels[target]]=[labels[target],labels[index]];
    return labels;
  }
  function filterValueHtml(property,rule,index,attribute){
    if(!['data-linked-pop-filter-value','data-db-view-filter-value','data-advanced-filter-value'].includes(attribute))throw Error('invalid filter control');
    const label=escape(property?.name||rule?.property||'속성');
    const value=Array.isArray(rule?.value)?rule.value.join(', '):String(rule?.value??'');
    const field=`${attribute}="${index}" aria-label="${label} 필터 값"`;
    const disabled=['empty','not_empty'].includes(rule?.operator)?' disabled':'';
    if(property?.type==='checkbox'){
      const choices=[['','선택하세요'],['true','체크됨'],['false','체크 안 됨']];
      if(value&&!choices.some(([key])=>key===value))choices.push([value,value]);
      return `<select ${field}${disabled}>${choices.map(([key,name])=>`<option value="${escape(key)}" ${key===value?'selected':''}>${escape(name)}</option>`).join('')}</select>`;
    }
    if(['select','status','multi_select'].includes(property?.type)){
      const source=Array.isArray(property.options)&&property.options.length
        ?property.options:property.config?.options||[];
      const choices=[...new Set((Array.isArray(source)?source:[]).map(item=>typeof item==='string'?item:item?.name||item?.id||'').filter(Boolean))];
      if(value&&!choices.includes(value))choices.push(value);
      if(choices.length)return `<select ${field}${disabled}><option value="" ${value?'':'selected'}>선택하세요</option>${choices.map(option=>`<option value="${escape(option)}" ${option===value?'selected':''}>${escape(option)}</option>`).join('')}</select>`;
    }
    const type=property?.type==='date'&&(!value||/^\d{4}-\d{2}-\d{2}$/.test(value))?'date'
      :property?.type==='number'&&(!value||Number.isFinite(Number(value)))?'number':'text';
    return `<input type="${type}" ${field} placeholder="값" value="${escape(value)}"${type==='number'?' step="any"':''}${disabled}>`;
  }
  function filterMatchesRule(item,rule,comparable){
    const property=String(rule?.property||''),operator=String(rule?.operator||'contains');
    const expected=String(rule?.value??'').toLowerCase();
    const actual=comparable(item?.[property]).toLowerCase();
    if(operator==='equals')return actual===expected;
    if(operator==='not_equals')return actual!==expected;
    if(operator==='not_contains')return !actual.includes(expected);
    if(operator==='greater_than')return actual!==''&&Number(actual)>Number(expected);
    if(operator==='less_than')return actual!==''&&Number(actual)<Number(expected);
    if(operator==='on_or_before')return Boolean(actual)&&actual<=expected;
    if(operator==='on_or_after')return Boolean(actual)&&actual>=expected;
    if(operator==='not_empty')return Boolean(actual);
    if(operator==='empty')return !actual;
    return actual.includes(expected);
  }
  function filterMatchesGroup(item,group,comparable,depth=1){
    if(!group||!Array.isArray(group.children)||depth>3)return true;
    const children=group.children;
    if(!children.length)return true;
    const match=child=>Array.isArray(child?.children)
      ?filterMatchesGroup(item,child,comparable,depth+1)
      :filterMatchesRule(item,child,comparable);
    return group.logic==='or'?children.some(match):children.every(match);
  }
  function filterGroupRuleCount(group){
    if(!group||!Array.isArray(group.children))return 0;
    return group.children.reduce((count,child)=>count+(Array.isArray(child?.children)?filterGroupRuleCount(child):1),0);
  }
  function validFilterGroup(group,properties){
    if(group===null)return true;
    const ids=new Set((properties||[]).map(item=>item?.id).filter(Boolean));
    let count=0;
    const visit=(node,depth)=>{
      if(!node||typeof node!=='object'||Array.isArray(node))return false;
      if(Object.hasOwn(node,'children')){
        if(depth>3||!['and','or'].includes(node.logic)||!Array.isArray(node.children)
            ||node.children.length>16||Object.keys(node).some(key=>!['logic','children'].includes(key)))return false;
        return node.children.every(child=>visit(child,depth+1));
      }
      count++;
      return depth<=4&&count<=16&&Object.keys(node).every(key=>['property','operator','value'].includes(key))
        &&ids.has(node.property)&&Object.hasOwn(FILTER_OPERATORS,node.operator)
        &&(typeof node.value==='string'&&node.value.length<=500
          ||typeof node.value==='number'&&Number.isFinite(node.value)
          ||typeof node.value==='boolean'
          ||Array.isArray(node.value)&&node.value.length<=16&&node.value.every(value=>typeof value==='string'&&value.length<=500));
    };
    return Boolean(group&&Array.isArray(group.children)&&visit(group,1));
  }
  function filterGroupWithoutProperty(group,propertyId){
    if(!group||!Array.isArray(group.children))return group;
    return {...group,children:group.children.flatMap(child=>{
      if(Array.isArray(child?.children))return [filterGroupWithoutProperty(child,propertyId)];
      return child?.property===propertyId?[]:[child];
    })};
  }
  function mountAdvancedFilterEditor(container,initial,properties){
    const available=(Array.isArray(properties)?properties:[]).filter(item=>item?.id);
    let tree=initial&&Array.isArray(initial.children)?clone(initial):{logic:'and',children:[]};
    const nodeAt=path=>String(path||'').split('.').filter(Boolean).reduce(
      (node,index)=>node?.children?.[Number(index)],tree);
    const renderNode=(node,path,depth)=>{
      if(Array.isArray(node?.children)){
        const logic=node.logic==='or'?'or':'and';
        return `<div class="advanced-filter-group" data-advanced-group="${path}"><div class="advanced-filter-group-heading"><label>조건 묶음 <select data-advanced-logic="${path}" aria-label="${depth}단계 조건 연결"><option value="and" ${logic==='and'?'selected':''}>모두 만족 (AND)</option><option value="or" ${logic==='or'?'selected':''}>하나 이상 만족 (OR)</option></select></label>${depth>1?`<button type="button" data-advanced-remove="${path}" aria-label="조건 묶음 삭제">×</button>`:''}</div><div class="advanced-filter-children">${node.children.map((child,index)=>renderNode(child,path?`${path}.${index}`:String(index),depth+1)).join('')}</div><div class="advanced-filter-actions"><button type="button" data-advanced-add-rule="${path}" ${filterGroupRuleCount(tree)>=16?'disabled':''}>＋ 조건 추가</button>${depth<3?`<button type="button" data-advanced-add-group="${path}">＋ 조건 묶음 추가</button>`:''}</div></div>`;
      }
      const property=available.find(item=>item.id===node?.property);
      const choices=available.map(item=>`<option value="${escape(item.id)}" ${item.id===node?.property?'selected':''}>${escape(item.name||item.id)}</option>`).join('');
      const missing=node?.property&&!property?`<option value="${escape(node.property)}" selected>삭제된 속성</option>`:'';
      return `<div class="advanced-filter-rule" data-advanced-rule="${path}"><select data-advanced-property="${path}" aria-label="필터 속성">${missing}${choices}</select><select data-advanced-operator="${path}" aria-label="필터 조건">${Object.entries(FILTER_OPERATORS).map(([key,label])=>`<option value="${key}" ${node?.operator===key?'selected':''}>${label}</option>`).join('')}</select>${filterValueHtml(property,node,path,'data-advanced-filter-value')}<button type="button" data-advanced-remove="${path}" aria-label="필터 삭제">×</button></div>`;
    };
    const draw=()=>{container.innerHTML=renderNode(tree,'',1);};
    container.addEventListener('input',event=>{
      const path=event.target?.getAttribute?.('data-advanced-filter-value');
      if(path!==null){const node=nodeAt(path);if(node&&!Array.isArray(node.children))node.value=event.target.value;}
    });
    container.addEventListener('change',event=>{
      const target=event.target;
      if(target.hasAttribute('data-advanced-logic')){
        const node=nodeAt(target.dataset.advancedLogic);
        if(node&&Array.isArray(node.children))node.logic=target.value==='or'?'or':'and';
      }else if(target.hasAttribute('data-advanced-property')){
        const path=target.dataset.advancedProperty,node=nodeAt(path);
        if(node&&!Array.isArray(node.children)){
          node.property=target.value;node.operator='contains';node.value='';draw();
          container.querySelector(`[data-advanced-filter-value="${path}"]`)?.focus();
        }
      }else if(target.hasAttribute('data-advanced-operator')){
        const path=target.dataset.advancedOperator,node=nodeAt(path);
        if(node&&!Array.isArray(node.children)){
          node.operator=target.value;draw();
          container.querySelector(`[data-advanced-operator="${path}"]`)?.focus();
        }
      }else if(target.hasAttribute('data-advanced-filter-value')){
        const node=nodeAt(target.dataset.advancedFilterValue);
        if(node&&!Array.isArray(node.children))node.value=target.value;
      }
    });
    container.addEventListener('click',event=>{
      const button=event.target.closest('button');
      if(!button||!container.contains(button))return;
      if(button.hasAttribute('data-advanced-add-rule')){
        const group=nodeAt(button.dataset.advancedAddRule);
        if(!Array.isArray(group?.children)||filterGroupRuleCount(tree)>=16||!available.length)return;
        group.children.push({property:available[0].id,operator:'contains',value:''});draw();
      }else if(button.hasAttribute('data-advanced-add-group')){
        const path=button.dataset.advancedAddGroup,group=nodeAt(path);
        if(!Array.isArray(group?.children)||path.split('.').filter(Boolean).length>=2)return;
        group.children.push({logic:'and',children:[]});draw();
      }else if(button.hasAttribute('data-advanced-remove')){
        const parts=button.dataset.advancedRemove.split('.'),index=Number(parts.pop());
        const parent=nodeAt(parts.join('.'));
        if(Array.isArray(parent?.children)&&Number.isInteger(index))parent.children.splice(index,1);
        draw();
      }
    });
    draw();
    return {get:()=>clone(tree)};
  }
  function orderedProperties(properties,order=[]){
    const list=Array.isArray(order)?order:[],ranks=new Map();
    list.forEach((id,index)=>{if(typeof id==='string'&&!ranks.has(id))ranks.set(id,index);});
    return properties.filter(property=>property?.id).map((property,index)=>({property,index})).sort((a,b)=>{
      if(a.property.type==='title'||b.property.type==='title')
        return (a.property.type==='title'?-1:0)-(b.property.type==='title'?-1:0);
      const aRank=ranks.has(a.property.id)?ranks.get(a.property.id):list.length+a.index;
      const bRank=ranks.has(b.property.id)?ranks.get(b.property.id):list.length+b.index;
      return aRank-bRank||a.index-b.index;
    }).map(item=>item.property);
  }
  function movedPropertyOrder(properties,order,source,target){
    const arranged=orderedProperties(properties,order);
    if(arranged.find(item=>item.id===source)?.type==='title'
        ||arranged.find(item=>item.id===target)?.type==='title')return null;
    const ids=arranged.map(item=>item.id),from=ids.indexOf(source),to=ids.indexOf(target);
    if(from<0||to<0||from===to)return null;
    ids.splice(from,1);ids.splice(to,0,source);
    return ids;
  }
  function viewMenuHtml(view,properties=[],icon=()=>'',label='설정 보기',rows=[],privatePending=false){
    const config=view?.config||{};
    const sorts=Array.isArray(config.sorts)?config.sorts:[];
    const filters=Array.isArray(config.filters)?config.filters:[];
    const advancedCount=filterGroupRuleCount(config.filter_group);
    const displayProperties=orderedProperties(properties,config.property_order);
    const board=view?.type==='board';
    const boardGroups=board?boardGroupEntriesForRows(rows,properties,config):null;
    const groupProperty=board?boardGroups.property:properties.find(property=>property.id===config.group_property);
    const groupEntries=sortGroupEntries(board?boardGroups.entries:groupEntriesForRows(rows,groupProperty),config.group_sort,config.group_order);
    const groupChoices=board?properties.filter(property=>['status','select','person','checkbox','multi_select'].includes(property.type)):properties;
    const count=properties.filter(property=>property.type==='title'||config.property_visibility?.[property.id]!==false).length;
    return `<div class="database-view-popover linked-view-popover" data-linked-view-popover popover="auto" role="dialog" aria-label="${escape(label)}">
      <div data-linked-view-main>
        <div class="database-view-popover-head"><strong>설정 보기</strong><button type="button" data-linked-pop-close aria-label="닫기">×</button></div>
        <label class="database-view-popover-name">보기 이름<input data-linked-pop-name maxlength="100" value="${escape(view?.name||TYPES[view?.type]||'표')}"></label>
        <button type="button" data-linked-pop-layout>${icon(view?.type||'table')}레이아웃 <span>${escape(TYPES[view?.type]||'표')} ›</span></button>
        <button type="button" data-linked-pop-properties>${icon('eye')}속성 표시 여부 <span>${count} ›</span></button>
        <button type="button" data-linked-pop-filter>${icon('filter')}필터 <span>›</span></button>
        <button type="button" data-linked-pop-sort>${icon('sort')}정렬 <span>›</span></button>
        <button type="button" data-linked-pop-group>${icon('group')}그룹화 <span>›</span></button>
        <button type="button" data-linked-pop-manage>${icon('settings')}보기 관리 <span>›</span></button>
        ${privatePending?'<div class="database-private-view-actions"><span>나에게만 적용 중</span><button type="button" data-linked-pop-private-publish>모두에게 저장</button><button type="button" data-linked-pop-private-reset>취소</button></div>':''}
      </div>
      <div data-linked-view-layout-panel hidden>
        <div class="database-view-popover-head"><button type="button" data-linked-pop-back aria-label="설정 보기로 돌아가기">‹</button><strong>레이아웃</strong><button type="button" data-linked-pop-close aria-label="닫기">×</button></div>
        <div class="database-view-layout-grid">${MENU_LAYOUTS.map(type=>`<button type="button" data-linked-pop-type="${type}" aria-pressed="${view?.type===type}">${icon(type)}<span>${escape(TYPES[type])}</span></button>`).join('')}</div>
        ${view?.type==='table'?TABLE_OPTIONS.map(([key,text])=>`<label class="database-view-display-option"><span>${text}</span><input type="checkbox" role="switch" data-linked-pop-display="${key}" ${config[key]===false?'':'checked'}></label>`).join(''):''}
      </div>
      <div data-linked-view-properties-panel hidden>
        <div class="database-view-popover-head"><button type="button" data-linked-pop-back aria-label="설정 보기로 돌아가기">‹</button><strong>속성 표시 여부</strong><button type="button" data-linked-pop-close aria-label="닫기">×</button></div>
        <input class="database-view-property-search" type="search" data-linked-pop-property-search placeholder="속성을 검색하세요" aria-label="속성 검색">
        <div class="database-view-property-section"><span>${view?.type==='table'?'표에 표시하기':'보기에 표시하기'}</span><button type="button" data-linked-pop-hide-all>모두 숨기기</button></div>
        <div class="database-view-property-list">${displayProperties.map(property=>`<div class="database-view-property-row" data-linked-pop-property-row data-view-property-id="${escape(property.id)}"><button type="button" class="database-view-property-handle" aria-label="${escape(property.name||property.id)} 속성 순서 이동" ${property.type==='title'?'disabled':'draggable="true"'}>⋮⋮</button><span class="database-view-property-icon" aria-hidden="true">${propertyGlyph(property.type,icon)}</span><span class="database-view-property-name">${escape(property.name||property.id)}</span><button type="button" data-linked-pop-visible="${escape(property.id)}" aria-label="${escape(property.name||property.id)} 속성 ${property.type==='title'||config.property_visibility?.[property.id]!==false?'숨기기':'표시하기'}" aria-pressed="${property.type==='title'||config.property_visibility?.[property.id]!==false}" ${property.type==='title'?'disabled':''}>${icon('eye')}</button></div>`).join('')}</div>
      </div>
      <div data-linked-view-group-panel hidden>
        <div class="database-view-popover-head"><button type="button" data-linked-pop-back aria-label="설정 보기로 돌아가기">‹</button><strong>그룹화 기준</strong><button type="button" data-linked-pop-close aria-label="닫기">×</button></div>
        <input class="database-view-property-search" type="search" data-linked-pop-group-search placeholder="속성을 검색하세요" aria-label="그룹화 속성 검색">
        <div class="database-view-choice-list">${board?'':`<button type="button" class="database-view-choice-row" data-linked-pop-group-choice="" aria-pressed="${!config.group_property}"><span class="database-view-property-icon" aria-hidden="true"></span><span class="database-view-property-name">없음</span><span class="database-view-choice-check">✓</span></button>`}${groupChoices.filter(property=>property?.id).map(property=>`<button type="button" class="database-view-choice-row" data-linked-pop-group-row data-linked-pop-group-choice="${escape(property.id)}" aria-pressed="${groupProperty?.id===property.id}"><span class="database-view-property-icon" aria-hidden="true">${propertyGlyph(property.type,icon)}</span><span class="database-view-property-name">${escape(property.name||property.id)}</span><span class="database-view-choice-check">✓</span></button>`).join('')}</div>
        ${groupProperty?`<label class="database-view-group-sort">그룹 정렬<select data-linked-pop-group-sort aria-label="그룹 정렬">${GROUP_SORTS.map(([mode,text])=>`<option value="${mode}" ${config.group_sort===mode||!config.group_sort&&mode==='manual'?'selected':''}>${text}</option>`).join('')}</select></label>${(!config.group_sort||config.group_sort==='manual')&&groupEntries.length>1?`<div class="database-view-group-order" aria-label="수동 그룹 순서">${groupEntries.map(([name],index)=>`<div class="database-view-group-order-row"><span>${escape(name)}</span><button type="button" data-linked-pop-group-move="${escape(name)}" data-group-direction="-1" aria-label="${escape(name)} 그룹 위로" ${index===0?'disabled':''}>↑</button><button type="button" data-linked-pop-group-move="${escape(name)}" data-group-direction="1" aria-label="${escape(name)} 그룹 아래로" ${index===groupEntries.length-1?'disabled':''}>↓</button></div>`).join('')}</div>`:''}`:''}
      </div>
      <div data-linked-view-sort-panel hidden>
        <div class="database-view-popover-head"><button type="button" data-linked-pop-back aria-label="설정 보기로 돌아가기">‹</button><strong>${sorts.length?'정렬':'새 정렬'}</strong><button type="button" data-linked-pop-close aria-label="닫기">×</button></div>
        ${sorts.length?`<div class="database-view-sort-rules">${sorts.map((rule,index)=>{const property=properties.find(item=>item.id===rule.property);return `<div class="database-view-sort-rule"><span class="database-view-property-icon" aria-hidden="true">${propertyGlyph(property?.type,icon)}</span><span class="database-view-property-name">${escape(property?.name||rule.property)}</span><select data-linked-pop-sort-direction="${index}" aria-label="${escape(property?.name||rule.property)} 정렬 방향"><option value="asc" ${rule.direction==='desc'?'':'selected'}>오름차순</option><option value="desc" ${rule.direction==='desc'?'selected':''}>내림차순</option></select><button type="button" data-linked-pop-sort-remove="${index}" aria-label="${escape(property?.name||rule.property)} 정렬 삭제">×</button></div>`;}).join('')}</div>`:''}
        <input class="database-view-property-search" type="search" data-linked-pop-sort-search placeholder="정렬 기준" aria-label="정렬 기준 검색">
        <div class="database-view-choice-list">${properties.filter(property=>property?.id&&!sorts.some(rule=>rule.property===property.id)).map(property=>`<button type="button" class="database-view-choice-row" data-linked-pop-sort-row data-linked-pop-sort-choice="${escape(property.id)}"><span class="database-view-property-icon" aria-hidden="true">${propertyGlyph(property.type,icon)}</span><span class="database-view-property-name">${escape(property.name||property.id)}</span></button>`).join('')}</div>
      </div>
      <div data-linked-view-filter-panel hidden>
        <div class="database-view-popover-head"><button type="button" data-linked-pop-back aria-label="설정 보기로 돌아가기">‹</button><strong>${filters.length||advancedCount?'필터':'필터 추가'}</strong><button type="button" data-linked-pop-close aria-label="닫기">×</button></div>
        ${advancedCount?`<p class="database-rules-note">고급 필터 · 조건 ${advancedCount}개 적용 중</p>`:''}
        ${advancedCount?'':`
        ${filters.length?`<div class="database-view-filter-rules">${filters.map((rule,index)=>{const property=properties.find(item=>item.id===rule.property);return `<div class="database-view-filter-rule"><span class="database-view-property-icon" aria-hidden="true">${propertyGlyph(property?.type,icon)}</span><span class="database-view-property-name">${escape(property?.name||rule.property)}</span><button type="button" data-linked-pop-filter-remove="${index}" aria-label="${escape(property?.name||rule.property)} 필터 삭제">×</button><select data-linked-pop-filter-operator="${index}" aria-label="${escape(property?.name||rule.property)} 필터 조건">${Object.entries(FILTER_OPERATORS).map(([key,text])=>`<option value="${key}" ${rule.operator===key?'selected':''}>${text}</option>`).join('')}</select>${filterValueHtml(property,rule,index,'data-linked-pop-filter-value')}</div>`;}).join('')}</div>`:''}
        <input class="database-view-property-search" type="search" data-linked-pop-filter-search placeholder="필터 기준" aria-label="필터 기준 검색">
        <div class="database-view-choice-list">${properties.filter(property=>property?.id).map(property=>`<button type="button" class="database-view-choice-row" data-linked-pop-filter-row data-linked-pop-filter-choice="${escape(property.id)}"><span class="database-view-property-icon" aria-hidden="true">${propertyGlyph(property.type,icon)}</span><span class="database-view-property-name">${escape(property.name||property.id)}</span></button>`).join('')}</div>`}
        <button type="button" class="database-view-filter-advanced" data-linked-pop-filter-advanced>${advancedCount?'고급 필터 편집':'＋ 고급 필터 추가'}</button>
      </div>
    </div>`;
  }
  function positionViewMenu(trigger,picker){
    if(!trigger?.isConnected||!picker?.isConnected||!picker.matches(':popover-open'))return;
    const width=Math.min(292,Math.max(0,innerWidth-16)),rect=trigger.getBoundingClientRect();
    picker.style.width=`${width}px`;
    picker.style.left=`${Math.max(8,Math.min(rect.right-width,innerWidth-width-8))}px`;
    picker.style.top=`${Math.max(8,Math.min(rect.bottom+4,innerHeight-picker.getBoundingClientRect().height-8))}px`;
  }
  function showViewMenu(trigger,picker,panel='main'){
    if(!trigger?.isConnected||!picker?.isConnected||!trigger.getClientRects().length)return;
    picker.querySelector('[data-linked-view-main]').hidden=panel!=='main';
    picker.querySelector('[data-linked-view-layout-panel]').hidden=panel!=='layout';
    picker.querySelector('[data-linked-view-properties-panel]').hidden=panel!=='properties';
    picker.querySelector('[data-linked-view-group-panel]').hidden=panel!=='group';
    picker.querySelector('[data-linked-view-sort-panel]').hidden=panel!=='sort';
    picker.querySelector('[data-linked-view-filter-panel]').hidden=panel!=='filter';
    if(!picker.matches(':popover-open'))picker.showPopover();
    positionViewMenu(trigger,picker);
    trigger.setAttribute('aria-expanded','true');
    (panel==='layout'?picker.querySelector('[data-linked-view-layout-panel] [data-linked-pop-back]')
      :panel==='properties'?picker.querySelector('[data-linked-pop-property-search]')
      :panel==='group'?picker.querySelector('[data-linked-pop-group-search]')
      :panel==='sort'?picker.querySelector('[data-linked-pop-sort-search]')
      :panel==='filter'?picker.querySelector('[data-linked-pop-filter-search]')
      :picker.querySelector('[data-linked-pop-layout]'))?.focus({preventScroll:true});
  }
  function filterViewProperties(picker,query=''){
    const term=String(query).trim().toLocaleLowerCase();
    picker.querySelectorAll('[data-linked-pop-property-row]').forEach(row=>{
      row.hidden=Boolean(term)&&!row.querySelector('.database-view-property-name')?.textContent?.toLocaleLowerCase().includes(term);
    });
  }
  function filterViewGroups(picker,query=''){
    const term=String(query).trim().toLocaleLowerCase();
    picker.querySelectorAll('[data-linked-pop-group-row]').forEach(row=>{
      row.hidden=Boolean(term)&&!row.querySelector('.database-view-property-name')?.textContent?.toLocaleLowerCase().includes(term);
    });
  }
  function filterViewSorts(picker,query=''){
    const term=String(query).trim().toLocaleLowerCase();
    picker.querySelectorAll('[data-linked-pop-sort-row]').forEach(row=>{
      row.hidden=Boolean(term)&&!row.querySelector('.database-view-property-name')?.textContent?.toLocaleLowerCase().includes(term);
    });
  }
  function filterViewFilters(picker,query=''){
    const term=String(query).trim().toLocaleLowerCase();
    picker.querySelectorAll('[data-linked-pop-filter-row]').forEach(row=>{
      row.hidden=Boolean(term)&&!row.querySelector('.database-view-property-name')?.textContent?.toLocaleLowerCase().includes(term);
    });
  }
  function bindViewPropertyOrder(picker,onMove){
    const rows=[...picker.querySelectorAll('[data-view-property-id]')];
    let dragging='';
    const clear=()=>rows.forEach(row=>row.classList.remove('database-view-property-drop-target'));
    rows.forEach((row,index)=>{
      const handle=row.querySelector('.database-view-property-handle');
      if(!handle||handle.disabled)return;
      handle.addEventListener('dragstart',event=>{
        dragging=row.dataset.viewPropertyId;
        event.dataTransfer.effectAllowed='move';
        event.dataTransfer.setData('text/plain',dragging);
        event.stopPropagation();
      });
      handle.addEventListener('dragend',event=>{dragging='';clear();event.stopPropagation();});
      row.addEventListener('dragover',event=>{
        if(!dragging||dragging===row.dataset.viewPropertyId)return;
        event.preventDefault();event.stopPropagation();
        clear();row.classList.add('database-view-property-drop-target');
        event.dataTransfer.dropEffect='move';
      });
      row.addEventListener('dragleave',()=>row.classList.remove('database-view-property-drop-target'));
      row.addEventListener('drop',event=>{
        event.preventDefault();event.stopPropagation();
        const source=dragging,target=row.dataset.viewPropertyId;
        dragging='';clear();
        if(source&&target&&source!==target)onMove(source,target);
      });
      handle.addEventListener('keydown',event=>{
        if(!['ArrowUp','ArrowDown'].includes(event.key))return;
        const target=rows[index+(event.key==='ArrowUp'?-1:1)];
        if(!target||target.querySelector('.database-view-property-handle')?.disabled)return;
        event.preventDefault();event.stopPropagation();
        onMove(row.dataset.viewPropertyId,target.dataset.viewPropertyId);
      });
    });
  }
  function localView(view){return {id:id(),name:String(view?.name||TYPES[view?.type]||'표'),type:TYPES[view?.type]?view.type:'table',config:Object.fromEntries(KEYS.filter(key=>Object.hasOwn(view?.config||{},key)).map(key=>[key,clone(view.config[key])]))};}
  function descriptor(blockId,source,view,layout={}){
    const local=localView(view);
    return {id:blockId,type:local.type,content:'',props:{...clone(layout),linked_database:{document_id:source.document_id,database_id:source.id},views:[local],active_view_id:local.id}};
  }
  function create(host){
    let generation=0,context=null,slots=[],abort=null,flight=null,menuState=null,templateMenuState=null;
    const current=(version,scope)=>version===generation&&context===scope&&host.current(scope);
    if(typeof document!=='undefined')document.addEventListener('pointerdown',event=>{
      const open=menuState;
      if(open?.picker?.isConnected&&!open.picker.contains(event.target)
          &&!open.trigger?.contains(event.target))menuState=null;
    },true);
    if(typeof window!=='undefined')window.addEventListener('resize',()=>{
      if(menuState?.picker?.matches(':popover-open'))positionViewMenu(menuState.trigger,menuState.picker);
    });
    function pause(){generation++;abort?.abort();abort=null;flight=null;}
    function clear(){pause();menuState=null;templateMenuState=null;for(const slot of slots){slot.closeRelationPicker?.(false);slot.closeFileMenu?.(false);slot.node.replaceChildren();}if(context&&!host.current(context))host.invalidated?.();}
    function dispose(){clear();context=null;slots=[];}
    function viewChange(slot,next){
      if(!host.canEdit(context)||!host.current(context))return false;
      return host.commit(context,slot.block, next);
    }
    function quickViewChange(slot,patch,panel='main',focusKey=null){
      if(!host.canEdit(context)||!host.current(context))return false;
      const next=clone(slot.block),view=next.props.views.find(item=>item.id===next.props.active_view_id);
      if(!view)return false;
      if(Object.hasOwn(patch,'name'))view.name=String(patch.name||TYPES[view.type]).slice(0,100);
      if(Object.hasOwn(patch,'type')&&TYPES[patch.type]){view.type=patch.type;next.type=patch.type;}
      if(patch.config)view.config={...view.config,...patch.config};
      menuState=menuState?.blockId===slot.id&&menuState.picker?.isConnected
        ?{pageId:context.pageId,blockId:slot.id,panel,focusKey,query:menuState.query||'',
          anchorKind:menuState.anchorKind}:null;
      if(patch.config && Object.keys(patch).length===1
          && Object.keys(patch.config).every(key=>['filters','filter_group','sorts'].includes(key))
          && host.privateViewChange){
        if(!host.privateViewChange(context,slot.id,view.id,patch.config))return false;
        if(slot.data)display(slot,slot.data);
        return true;
      }
      if(viewChange(slot,next))return true;
      menuState=null;return false;
    }
    function settings(slot,data,viewId,add=false){
      if(!host.canEdit(context))return;
      const scope=context,original=slot.block,views=clone(original.props.views);
      const storedView=views.find(v=>v.id===viewId);
      const view=add?localView({type:'table',name:'표',config:{}})
        :storedView?{...storedView,config:{...storedView.config,
          ...(host.privateViewConfig?.(scope,slot.id,viewId)||{})}}:null;
      if(!view||add&&views.length>=64)return;
      const properties=data.database.properties||[];
      let filters=clone(view.config.filters||[]),sorts=clone(view.config.sorts||[]);
      const options=(value,empty=false)=>(empty?'<option value="">없음</option>':'')+properties.map(p=>`<option value="${escape(p.id)}" ${p.id===value?'selected':''}>${escape(p.name)}</option>`).join('');
      const ops={contains:'포함',not_contains:'포함하지 않음',equals:'같음',not_equals:'같지 않음',greater_than:'보다 큼',less_than:'보다 작음',empty:'비어 있음',not_empty:'비어 있지 않음',on_or_before:'이전 또는 같음',on_or_after:'이후 또는 같음'};
      const form=host.modal(add?'새 연결 보기':'연결 보기 설정',`<div class="linked-view-settings">
        <label>보기 이름<input data-linked-name value="${escape(view.name)}" maxlength="100"></label>
        <label>레이아웃<select data-linked-type>${Object.entries(TYPES).map(([key,name])=>`<option value="${key}" ${view.type===key?'selected':''}>${name}</option>`).join('')}</select></label>
        <fieldset><legend>필터</legend><p class="database-rules-note">${view.config?.filter_group?'고급 필터가 적용 중입니다. 필터 메뉴에서 편집하세요.':'모든 조건을 만족하는 항목만 표시합니다.'}</p><div data-linked-filters></div><button type="button" data-linked-filter-add ${view.config?.filter_group?'disabled':''}>필터 추가</button></fieldset>
        <fieldset><legend>정렬</legend><div data-linked-sorts></div><button type="button" data-linked-sort-add>정렬 추가</button></fieldset>
        <label>그룹<select data-linked-group>${options(view.type==='board'?view.config.board_group_property:view.config.group_property,true)}</select></label>
        <label>그룹 정렬<select data-linked-group-sort>${GROUP_SORTS.map(([mode,text])=>`<option value="${mode}" ${view.config.group_sort===mode||!view.config.group_sort&&mode==='manual'?'selected':''}>${text}</option>`).join('')}</select></label>
        <fieldset><legend>표시할 속성</legend>${properties.map(p=>`<label><input type="checkbox" data-linked-visible="${escape(p.id)}" ${view.config.property_visibility?.[p.id]===false?'':'checked'}>${escape(p.name)}</label>`).join('')}</fieldset>
        <fieldset data-linked-table-options><legend>표 레이아웃</legend>${TABLE_OPTIONS.map(([key,label])=>`<label class="database-view-display-option"><span>${label}</span><input type="checkbox" role="switch" data-linked-display="${key}" ${view.config[key]===false?'':'checked'}></label>`).join('')}</fieldset></div>`,[
          ...(!add?[{label:'보기 복제',action:()=>save('duplicate')},...(views.length>1?[{label:'보기 삭제',action:()=>save('delete')}]:[])]:[]),
          {label:'취소',action:host.closeModal},{label:'적용',primary:true,action:()=>save('save')},
        ]);
      function renderRules(){
        form.querySelector('[data-linked-filters]').innerHTML=filters.map((f,i)=>`<div class="linked-rule"><select data-lf-property="${i}" aria-label="필터 속성">${options(f.property)}</select><select data-lf-operator="${i}" aria-label="필터 조건">${Object.entries(ops).map(([k,v])=>`<option value="${k}" ${f.operator===k?'selected':''}>${v}</option>`).join('')}</select><input data-lf-value="${i}" aria-label="필터 값" value="${escape(f.value??'')}"><button type="button" data-lf-remove="${i}" aria-label="필터 삭제">×</button></div>`).join('');
        form.querySelector('[data-linked-sorts]').innerHTML=sorts.map((s,i)=>`<div class="linked-rule"><select data-ls-property="${i}" aria-label="정렬 속성">${options(s.property)}</select><select data-ls-direction="${i}" aria-label="정렬 방향"><option value="asc" ${s.direction==='desc'?'':'selected'}>오름차순</option><option value="desc" ${s.direction==='desc'?'selected':''}>내림차순</option></select><button type="button" data-ls-remove="${i}" aria-label="정렬 삭제">×</button></div>`).join('');
        form.querySelectorAll('[data-lf-remove]').forEach(button=>button.onclick=()=>{collect();filters.splice(Number(button.dataset.lfRemove),1);renderRules();});
        form.querySelectorAll('[data-ls-remove]').forEach(button=>button.onclick=()=>{collect();sorts.splice(Number(button.dataset.lsRemove),1);renderRules();});
      }
      function collect(){
        filters=filters.map((f,i)=>{const property=form.querySelector(`[data-lf-property="${i}"]`).value,raw=form.querySelector(`[data-lf-value="${i}"]`).value;
          const type=properties.find(p=>p.id===property)?.type;
          let value=raw===String(f.value??'')&&property===f.property?f.value:raw;
          if(raw!==String(f.value??'')||property!==f.property){if(type==='number'&&raw!==''&&Number.isFinite(Number(raw)))value=Number(raw);else if(['relation','multi_select','person'].includes(type))value=raw.split(',').map(v=>v.trim()).filter(Boolean);else if(type==='checkbox')value=['true','1','예'].includes(raw.toLowerCase());}
          return {...f,property,operator:form.querySelector(`[data-lf-operator="${i}"]`).value,value:value??''};});
        sorts=sorts.map((s,i)=>({...s,property:form.querySelector(`[data-ls-property="${i}"]`).value,direction:form.querySelector(`[data-ls-direction="${i}"]`).value}));
      }
      function save(mode){
        if(!host.current(scope)||!form.isConnected)return;
        collect();
        const board=form.querySelector('[data-linked-type]').value==='board';
        const selectedGroup=form.querySelector('[data-linked-group]').value;
        const previousGroup=board?view.config.board_group_property:view.config.group_property;
        const next=clone(original),changed={...view,name:form.querySelector('[data-linked-name]').value.trim()||TYPES[view.type],type:form.querySelector('[data-linked-type]').value,
          config:{...view.config,filters,sorts,group_property:board?view.config.group_property||'':selectedGroup,
            board_group_property:board?selectedGroup:view.config.board_group_property||'',
            group_sort:form.querySelector('[data-linked-group-sort]').value,
            group_order:selectedGroup===(previousGroup||'')?view.config.group_order||[]:[],
            property_visibility:Object.fromEntries([...form.querySelectorAll('[data-linked-visible]')].map(input=>[input.dataset.linkedVisible,input.checked])),...Object.fromEntries([...form.querySelectorAll('[data-linked-display]')].map(input=>[input.dataset.linkedDisplay,input.checked]))}};
        if(mode==='delete')next.props.views=views.filter(v=>v.id!==view.id);
        else if(add||mode==='duplicate'){if(views.length>=64)return;if(mode==='duplicate'){changed.id=id();changed.name+=' 사본';}next.props.views=[...views,changed];}
        else {
          const shared=clone(changed);
          if(host.privateViewChange){
            for(const key of ['filters','filter_group','sorts']){
              if(Object.hasOwn(storedView.config||{},key))shared.config[key]=clone(storedView.config[key]);
              else delete shared.config[key];
            }
          }
          next.props.views=views.map(v=>v.id===view.id?shared:v);
        }
        next.props.active_view_id=mode==='delete'?next.props.views[0].id:changed.id;
        next.type=next.props.views.find(v=>v.id===next.props.active_view_id).type;
        if(!add&&mode!=='delete'&&mode!=='duplicate'&&host.privateViewChange){
          if(JSON.stringify(next)!==JSON.stringify(original)&&!viewChange(slot,next))return;
          const privatePatch={};
          if(JSON.stringify(filters)!==JSON.stringify(view.config.filters||[]))privatePatch.filters=filters;
          if(JSON.stringify(sorts)!==JSON.stringify(view.config.sorts||[]))privatePatch.sorts=sorts;
          if(Object.keys(privatePatch).length){
            if(!host.privateViewChange(scope,slot.id,view.id,privatePatch))return;
            if(slot.data)display(slot,slot.data);
          }
          host.closeModal();return;
        }
        if(viewChange(slot,next))host.closeModal();
      }
      form.querySelector('[data-linked-filter-add]').onclick=()=>{collect();filters.push({property:properties[0]?.id||'',operator:'contains',value:''});renderRules();};
      form.querySelector('[data-linked-sort-add]').onclick=()=>{collect();sorts.push({property:properties[0]?.id||'',direction:'asc'});renderRules();};
      const showTableOptions=()=>{
        const type=form.querySelector('[data-linked-type]').value;
        form.querySelector('[data-linked-table-options]').hidden=type!=='table';
        const group=form.querySelector('[data-linked-group]');
        for(const option of group.options){
          const property=properties.find(item=>item.id===option.value);
          option.disabled=type==='board'&&Boolean(option.value)
            &&!['status','select','person','checkbox','multi_select'].includes(property?.type);
        }
        if(type==='board'&&group.selectedOptions[0]?.disabled)
          group.value=properties.find(item=>['status','select','person','checkbox','multi_select'].includes(item.type))?.id||'';
      };
      form.querySelector('[data-linked-type]').addEventListener('change',showTableOptions);
      showTableOptions();
      renderRules();
      return form;
    }
    function advancedSettings(slot,data){
      if(!host.canEdit(context))return;
      const view=slot.block.props.views.find(item=>item.id===slot.block.props.active_view_id);
      if(!view)return;
      const config={...view.config,...(host.privateViewConfig?.(context,slot.id,view.id)||{})};
      const initial=config.filter_group||{logic:'and',children:clone(config.filters||[])};
      let editor;
      const form=host.modal('고급 필터',`<p class="database-rules-note">AND/OR 조건 묶음은 3단계까지 만들 수 있습니다.</p><div class="advanced-filter-editor" data-advanced-filter-editor></div>`,[
        {label:'필터 지우기',action:()=>{
          const current=slots.find(item=>item.id===slot.id);
          if(current&&quickViewChange(current,{config:{filters:[],filter_group:null}},'filter'))host.closeModal();
        }},
        {label:'취소',action:host.closeModal},
        {label:'적용',primary:true,action:()=>{
          const current=slots.find(item=>item.id===slot.id);
          if(current&&editor&&quickViewChange(current,{config:{filters:[],filter_group:editor.get()}},'filter'))host.closeModal();
        }},
      ]);
      editor=mountAdvancedFilterEditor(form.querySelector('[data-advanced-filter-editor]'),initial,data.database.properties||[]);
      return form;
    }
    function bindViewMenu(slot,data,projected){
      const trigger=slot.node.querySelector('[data-linked-settings]');
      const filterShortcut=slot.node.querySelector('[data-linked-toolbar-filter]');
      const sortShortcut=slot.node.querySelector('[data-linked-toolbar-sort]');
      const picker=slot.node.querySelector('[data-linked-view-popover]');
      if(!trigger||!picker)return;
      const show=(panel='main',focusKey=null,query=menuState?.panel===panel?menuState.query||'':'',anchor=trigger)=>{
        const anchorKind=anchor===filterShortcut?'filter':anchor===sortShortcut?'sort':'settings';
        menuState={pageId:context.pageId,blockId:slot.id,panel,focusKey,query,trigger:anchor,picker,anchorKind};
        showViewMenu(anchor,picker,panel);
        for(const button of [trigger,filterShortcut,sortShortcut])button?.setAttribute('aria-expanded',
          String(button===anchor));
        const search=picker.querySelector('[data-linked-pop-property-search]');
        if(search)search.value=panel==='properties'?query:'';
        filterViewProperties(picker,query);
        const groupSearch=picker.querySelector('[data-linked-pop-group-search]');
        if(groupSearch)groupSearch.value=panel==='group'?query:'';
        filterViewGroups(picker,panel==='group'?query:'');
        const sortSearch=picker.querySelector('[data-linked-pop-sort-search]');
        if(sortSearch)sortSearch.value=panel==='sort'?query:'';
        filterViewSorts(picker,panel==='sort'?query:'');
        const filterSearch=picker.querySelector('[data-linked-pop-filter-search]');
        if(filterSearch)filterSearch.value=panel==='filter'?query:'';
        filterViewFilters(picker,panel==='filter'?query:'');
        if(focusKey?.startsWith('property:'))picker.querySelector(`[data-linked-pop-visible="${CSS.escape(focusKey.slice(9))}"]`)?.focus({preventScroll:true});
        else if(focusKey?.startsWith('order:'))picker.querySelector(`[data-view-property-id="${CSS.escape(focusKey.slice(6))}"] .database-view-property-handle`)?.focus({preventScroll:true});
        else if(focusKey?.startsWith('group:'))picker.querySelector(`[data-linked-pop-group-choice="${CSS.escape(focusKey.slice(6))}"]`)?.focus({preventScroll:true});
        else if(focusKey==='group-sort')picker.querySelector('[data-linked-pop-group-sort]')?.focus({preventScroll:true});
        else if(focusKey?.startsWith('group-move:'))picker.querySelector(`[data-linked-pop-group-move="${CSS.escape(focusKey.slice(11))}"]`)?.focus({preventScroll:true});
        else if(focusKey?.startsWith('sort-direction:'))picker.querySelector(`[data-linked-pop-sort-direction="${CSS.escape(focusKey.slice(15))}"]`)?.focus({preventScroll:true});
        else if(focusKey==='sort-search')sortSearch?.focus({preventScroll:true});
        else if(focusKey?.startsWith('filter-value:'))picker.querySelector(`[data-linked-pop-filter-value="${CSS.escape(focusKey.slice(13))}"]`)?.focus({preventScroll:true});
        else if(focusKey?.startsWith('filter-operator:'))picker.querySelector(`[data-linked-pop-filter-operator="${CSS.escape(focusKey.slice(16))}"]`)?.focus({preventScroll:true});
        else if(focusKey==='filter-search')filterSearch?.focus({preventScroll:true});
        else if(focusKey==='all-hide')picker.querySelector('[data-linked-pop-hide-all]')?.focus({preventScroll:true});
        else if(focusKey)picker.querySelector(`[data-linked-pop-display="${focusKey}"]`)?.focus({preventScroll:true});
      };
      const close=(focus=false)=>{
        const anchor=menuState?.trigger||trigger;
        menuState=null;
        if(picker.matches(':popover-open'))picker.hidePopover();
        if(focus&&anchor.isConnected)anchor.focus({preventScroll:true});
      };
      trigger.addEventListener('click',()=>picker.matches(':popover-open')?close():show());
      filterShortcut?.addEventListener('click',()=>
        picker.matches(':popover-open')&&menuState?.trigger===filterShortcut?close():show('filter','filter-search','',filterShortcut));
      sortShortcut?.addEventListener('click',()=>
        picker.matches(':popover-open')&&menuState?.trigger===sortShortcut?close():show('sort','sort-search','',sortShortcut));
      picker.addEventListener('toggle',event=>{
        for(const button of [trigger,filterShortcut,sortShortcut])button?.setAttribute('aria-expanded',
          String(event.newState==='open'&&menuState?.trigger===button));
        if(event.newState==='closed'&&picker.isConnected&&menuState?.picker===picker)menuState=null;
      });
      picker.querySelectorAll('[data-linked-pop-close]').forEach(button=>button.addEventListener('click',()=>close(true)));
      picker.querySelector('[data-linked-pop-private-publish]')?.addEventListener('click',()=>{
        if(!host.canEdit(context)||!host.current(context))return;
        const view=slot.block.props.views.find(item=>item.id===slot.block.props.active_view_id);
        const patch=host.privateViewConfig?.(context,slot.id,view?.id)||{};
        if(!view||!Object.keys(patch).length)return;
        const next=clone(slot.block);
        const target=next.props.views.find(item=>item.id===view.id);
        target.config={...target.config,...patch};
        close();
        if(viewChange(slot,next))host.privateViewClear?.(context,slot.id,view.id);
      });
      picker.querySelector('[data-linked-pop-private-reset]')?.addEventListener('click',()=>{
        const viewId=slot.block.props.active_view_id;
        close();
        host.privateViewClear?.(context,slot.id,viewId);
        if(slot.data)display(slot,slot.data);
      });
      picker.querySelector('[data-linked-pop-layout]')?.addEventListener('click',()=>show('layout'));
      picker.querySelector('[data-linked-pop-properties]')?.addEventListener('click',()=>show('properties'));
      picker.querySelector('[data-linked-pop-group]')?.addEventListener('click',()=>show('group'));
      picker.querySelector('[data-linked-pop-sort]')?.addEventListener('click',()=>show('sort'));
      picker.querySelector('[data-linked-pop-filter]')?.addEventListener('click',()=>show('filter'));
      picker.querySelectorAll('[data-linked-pop-back]').forEach(button=>button.addEventListener('click',()=>show()));
      picker.addEventListener('keydown',event=>{
        if(event.key!=='Escape'||event.isComposing)return;
        if(!picker.querySelector('[data-linked-view-main]').hidden){menuState=null;return;}
        event.preventDefault();event.stopPropagation();show();
      });
      picker.querySelector('[data-linked-pop-property-search]')?.addEventListener('input',event=>{
        if(menuState?.picker===picker)menuState.query=event.target.value;
        filterViewProperties(picker,event.target.value);
      });
      picker.querySelector('[data-linked-pop-group-search]')?.addEventListener('input',event=>{
        if(menuState?.picker===picker)menuState.query=event.target.value;
        filterViewGroups(picker,event.target.value);
      });
      picker.querySelector('[data-linked-pop-sort-search]')?.addEventListener('input',event=>{
        if(menuState?.picker===picker)menuState.query=event.target.value;
        filterViewSorts(picker,event.target.value);
      });
      picker.querySelector('[data-linked-pop-filter-search]')?.addEventListener('input',event=>{
        if(menuState?.picker===picker)menuState.query=event.target.value;
        filterViewFilters(picker,event.target.value);
      });
      const activeFilters=()=>{
        const view=slot.block.props.views.find(item=>item.id===slot.block.props.active_view_id);
        const config={...view?.config,...(host.privateViewConfig?.(context,slot.id,view?.id)||{})};
        return Array.isArray(config.filters)?config.filters:[];
      };
      picker.querySelectorAll('[data-linked-pop-filter-choice]').forEach(button=>button.addEventListener('click',()=>{
        const property=button.dataset.linkedPopFilterChoice;
        if(!(data.database.properties||[]).some(item=>item.id===property))return;
        const filters=activeFilters();
        if(filters.length>=16)return;
        quickViewChange(slot,{config:{filters:[...filters,{property,operator:'contains',value:''}]}},'filter',`filter-value:${filters.length}`);
      }));
      picker.querySelectorAll('[data-linked-pop-filter-operator]').forEach(input=>input.addEventListener('change',()=>{
        const index=Number(input.dataset.linkedPopFilterOperator),filters=activeFilters();
        if(!Number.isInteger(index)||index<0||index>=filters.length||!Object.hasOwn(FILTER_OPERATORS,input.value))return;
        quickViewChange(slot,{config:{filters:filters.map((rule,i)=>i===index?{...rule,operator:input.value}:rule)}},'filter',`filter-operator:${index}`);
      }));
      picker.querySelectorAll('[data-linked-pop-filter-value]').forEach(input=>input.addEventListener('change',()=>{
        const index=Number(input.dataset.linkedPopFilterValue),filters=activeFilters();
        if(!Number.isInteger(index)||index<0||index>=filters.length)return;
        quickViewChange(slot,{config:{filters:filters.map((rule,i)=>i===index?{...rule,value:input.value}:rule)}},'filter',`filter-value:${index}`);
      }));
      picker.querySelectorAll('[data-linked-pop-filter-remove]').forEach(button=>button.addEventListener('click',()=>{
        const index=Number(button.dataset.linkedPopFilterRemove),filters=activeFilters();
        if(!Number.isInteger(index)||index<0||index>=filters.length)return;
        quickViewChange(slot,{config:{filters:filters.filter((_,i)=>i!==index)}},'filter','filter-search');
      }));
      const activeSorts=()=>{
        const view=slot.block.props.views.find(item=>item.id===slot.block.props.active_view_id);
        const config={...view?.config,...(host.privateViewConfig?.(context,slot.id,view?.id)||{})};
        return Array.isArray(config.sorts)?config.sorts:[];
      };
      picker.querySelectorAll('[data-linked-pop-sort-choice]').forEach(button=>button.addEventListener('click',()=>{
        const property=button.dataset.linkedPopSortChoice;
        if(!(data.database.properties||[]).some(item=>item.id===property))return;
        const sorts=activeSorts();
        if(sorts.length>=16||sorts.some(rule=>rule.property===property))return;
        quickViewChange(slot,{config:{sorts:[...sorts,{property,direction:'asc'}]}},'sort','sort-search');
      }));
      picker.querySelectorAll('[data-linked-pop-sort-direction]').forEach(input=>input.addEventListener('change',()=>{
        const index=Number(input.dataset.linkedPopSortDirection),sorts=activeSorts();
        if(!Number.isInteger(index)||index<0||index>=sorts.length)return;
        quickViewChange(slot,{config:{sorts:sorts.map((rule,i)=>i===index?{...rule,direction:input.value}:rule)}},'sort',`sort-direction:${index}`);
      }));
      picker.querySelectorAll('[data-linked-pop-sort-remove]').forEach(button=>button.addEventListener('click',()=>{
        const index=Number(button.dataset.linkedPopSortRemove),sorts=activeSorts();
        if(!Number.isInteger(index)||index<0||index>=sorts.length)return;
        quickViewChange(slot,{config:{sorts:sorts.filter((_,i)=>i!==index)}},'sort','sort-search');
      }));
      picker.querySelectorAll('[data-linked-pop-group-choice]').forEach(button=>button.addEventListener('click',()=>{
        const value=button.dataset.linkedPopGroupChoice;
        const view=slot.block.props.views.find(item=>item.id===slot.block.props.active_view_id);
        const board=view?.type==='board';
        const properties=(data.database.properties||[]).filter(property=>!board
          ||['status','select','person','checkbox','multi_select'].includes(property.type));
        if(board&&!value||value&&!properties.some(property=>property.id===value))return;
        const key=board?'board_group_property':'group_property';
        const current=board?(view?.config?.board_group_property||properties[0]?.id||''):(view?.config?.group_property||'');
        if(current!==value)quickViewChange(slot,{config:{[key]:value,group_order:[]}},'group',`group:${value}`);
      }));
      picker.querySelector('[data-linked-pop-group-sort]')?.addEventListener('change',event=>{
        const mode=event.target.value;
        if(!GROUP_SORTS.some(([key])=>key===mode))return;
        quickViewChange(slot,{config:{group_sort:mode}},'group','group-sort');
      });
      picker.querySelectorAll('[data-linked-pop-group-move]').forEach(button=>button.addEventListener('click',()=>{
        const view=slot.block.props.views.find(item=>item.id===slot.block.props.active_view_id);
        const properties=data.database.properties||[];
        const entries=view?.type==='board'
          ?boardGroupEntriesForRows(projected.props?.rows||[],properties,view.config).entries
          :groupEntriesForRows(projected.props?.rows||[],properties.find(item=>item.id===view?.config?.group_property));
        const order=movedGroupOrder(entries,'manual',view?.config?.group_order,button.dataset.linkedPopGroupMove,Number(button.dataset.groupDirection));
        if(order)quickViewChange(slot,{config:{group_order:order}},'group',`group-move:${button.dataset.linkedPopGroupMove}`);
      }));
      const propertyVisibility=()=>{
        const view=slot.block.props.views.find(item=>item.id===slot.block.props.active_view_id);
        return Object.fromEntries((data.database.properties||[]).filter(property=>property?.id)
          .map(property=>[property.id,property.type==='title'||view?.config?.property_visibility?.[property.id]!==false]));
      };
      picker.querySelectorAll('[data-linked-pop-visible]').forEach(button=>button.addEventListener('click',()=>{
        const id=button.dataset.linkedPopVisible;
        const visibility=propertyVisibility();
        if(!Object.hasOwn(visibility,id)||button.disabled)return;
        visibility[id]=!visibility[id];
        quickViewChange(slot,{config:{property_visibility:visibility}},'properties',`property:${id}`);
      }));
      picker.querySelector('[data-linked-pop-hide-all]')?.addEventListener('click',()=>{
        const visibility=Object.fromEntries((data.database.properties||[]).filter(property=>property?.id)
          .map(property=>[property.id,property.type==='title']));
        quickViewChange(slot,{config:{property_visibility:visibility}},'properties','all-hide');
      });
      bindViewPropertyOrder(picker,(source,target)=>{
        const view=slot.block.props.views.find(item=>item.id===slot.block.props.active_view_id);
        const order=movedPropertyOrder(data.database.properties||[],view?.config?.property_order,source,target);
        if(order)quickViewChange(slot,{config:{property_order:order}},'properties',`order:${source}`);
      });
      picker.querySelector('[data-linked-pop-name]')?.addEventListener('change',event=>{
        const name=event.target.value.trim()||TYPES[slot.block.type]||'표';
        event.target.value=name;
        if(name!==slot.block.props.views.find(view=>view.id===slot.block.props.active_view_id)?.name)
          quickViewChange(slot,{name},'main','name');
      });
      picker.querySelectorAll('[data-linked-pop-type]').forEach(button=>button.addEventListener('click',()=>{
        const type=button.dataset.linkedPopType;
        if(type!==slot.block.type)quickViewChange(slot,{type},'layout');
      }));
      picker.querySelectorAll('[data-linked-pop-display]').forEach(input=>input.addEventListener('change',()=>{
        quickViewChange(slot,{config:{[input.dataset.linkedPopDisplay]:input.checked}},
          'layout',input.dataset.linkedPopDisplay);
      }));
      picker.querySelector('[data-linked-pop-filter-advanced]')?.addEventListener('click',()=>{
        close();advancedSettings(slot,data)?.querySelector('[data-advanced-add-rule]')?.focus({preventScroll:true});
      });
      for(const [selector,focus] of [
        ['[data-linked-pop-manage]','[data-linked-name]'],
      ])picker.querySelector(selector)?.addEventListener('click',()=>{
        close();const form=settings(slot,data,slot.block.props.active_view_id);
        form?.querySelector(focus)?.focus({preventScroll:true});
      });
      if(menuState?.pageId===context.pageId&&menuState.blockId===slot.id){
        const restore={...menuState};
        requestAnimationFrame(()=>{
          if(!host.current(context)||!picker.isConnected||menuState?.blockId!==slot.id)return;
          const anchor=restore.anchorKind==='filter'?filterShortcut
            :restore.anchorKind==='sort'?sortShortcut:trigger;
          show(restore.panel,restore.focusKey,restore.query,anchor||trigger);
          if(restore.focusKey==='name')picker.querySelector('[data-linked-pop-name]')?.focus({preventScroll:true});
        });
      }
    }
    function display(slot,data,restoredHourScroll){
      slot.closeRelationPicker?.(false);
      slot.closeFileMenu?.(false);
      const scope=context,editable=host.canEdit(scope),cellEditable=Boolean(host.canEditCells?.(scope,data));
      const rowEditable=Boolean(host.canCreateRows?.(scope,data));
      const previousHourScroll=slot.node.querySelector('.timeline-block.hour-scale .timeline-scroll')?.scrollLeft;
      // Local durable/pending view settings belong to the instance. The read
      // response supplies shared data, never an overwrite of an unsaved view.
      const displayData={...data,views:clone(slot.block.props.views),active_view_id:slot.block.props.active_view_id};
      const storedView=displayData.views.find(view=>view.id===displayData.active_view_id);
      const privateConfig=host.privateViewConfig?.(scope,slot.id,storedView?.id)||{};
      if(storedView&&Object.keys(privateConfig).length)
        storedView.config={...storedView.config,...privateConfig};
      const projected=host.project(displayData,slot.block,scope.pageId);
      const searchText=host.searchValue?.(scope,projected)||'';
      const searchOpen=Boolean(slot.searchOpen||searchText);
      const fullPage=Boolean(host.fullPage?.(scope,slot.id));
      const fullPageControl=!host.fullPage?'':fullPage
        ?'<button type="button" data-linked-full-page data-linked-close-full-page aria-label="인라인 연결 보기로 돌아가기" title="인라인으로 돌아가기">×</button>'
        :host.fullPageUrl
        ?`<a href="${escape(host.fullPageUrl(scope,slot.id))}" data-linked-full-page data-linked-open-full-page aria-label="연결 보기 전체 페이지로 열기" title="전체 페이지로 열기">↗</a>`
        :'<button type="button" data-linked-full-page data-linked-open-full-page aria-label="연결 보기 전체 페이지로 열기" title="전체 페이지로 열기">↗</button>';
      const localView=displayData.views.find(v=>v.id===displayData.active_view_id);
      slot.node.classList.toggle('linked-hide-source-title',localView?.config?.show_source_title===false);
      host.beforeDisplay?.(slot,data);
      slot.node.innerHTML=`<div class="linked-database-toolbar"><button type="button" class="linked-source" data-linked-source title="원본 데이터베이스 열기" aria-label="원본 데이터베이스 열기">↗ <span class="linked-source-name">${escape(data.database.title||'데이터베이스')}</span></button><button type="button" data-linked-refresh aria-label="연결된 데이터베이스 새로고침">↻</button></div><div class="database-toolbar"><div class="database-view-tabs" role="tablist" aria-label="연결된 데이터베이스 보기">${displayData.views.map(v=>`<button type="button" role="tab" data-linked-view="${escape(v.id)}" aria-selected="${v.id===displayData.active_view_id}" class="${v.id===displayData.active_view_id?'active':''}">${escape(v.name)}</button>`).join('')}${editable?'<button type="button" data-linked-add aria-label="연결 보기 추가">＋</button>':''}</div>${editable?`<button type="button" data-linked-toolbar-filter aria-label="필터" aria-haspopup="dialog" aria-expanded="false" title="필터">${host.icon?.('filter')||'☷'}</button><button type="button" data-linked-toolbar-sort aria-label="정렬" aria-haspopup="dialog" aria-expanded="false" title="정렬">${host.icon?.('sort')||'⇅'}</button>`:''}${host.searchValue?`<button type="button" data-linked-toolbar-search aria-label="검색" aria-expanded="${searchOpen}" title="검색">${host.icon?.('search')||'⌕'}</button><input type="search" data-linked-search aria-label="연결 보기 검색" placeholder="검색" value="${escape(searchText)}" ${searchOpen?'':'hidden'}>`:''}${fullPageControl}${editable?`<button type="button" data-linked-settings aria-label="보기 설정" aria-haspopup="dialog" aria-expanded="false" title="보기 설정">${host.icon?.('settings')||'⚙'}</button>`:''}${rowEditable?rowCreateHtml(data,slot.id,Boolean(host.canCreateTemplates?.(scope,data)),host.icon):''}</div>${editable?viewMenuHtml(localView,data.database.properties||[],host.icon,'설정 보기',projected.props?.rows||[],Object.keys(privateConfig).length>0):''}<div data-linked-row-status role="status" hidden></div><div data-linked-template-status role="status" hidden></div><div data-linked-save-status role="status" hidden></div><div data-linked-body>${host.render(projected,cellEditable)}</div>`;
      const body=slot.node.querySelector('[data-linked-body]');
      const hourScroll=body.querySelector?.('.timeline-block.hour-scale .timeline-scroll');
      if(hourScroll){const nowSlot=Number(hourScroll.dataset.timelineNowSlot);
        hourScroll.scrollLeft=restoredHourScroll??previousHourScroll??(hourScroll.hasAttribute('data-timeline-now-slot')
          &&Number.isFinite(nowSlot)?Math.max(0,nowSlot*72-hourScroll.clientWidth*.8):0);
        globalThis.NotionTimelineHourWindow?.bind(hourScroll,slot.id,
          projected.props?.timeline_anchor||host.dates.iso(new Date()),left=>display(slot,data,left));}
      body.querySelectorAll('.database-head,.database-toolbar,.board-view-controls,.database-add-property,.database-add-property-ghost,.calendar-add').forEach(node=>node.remove());
      body.querySelectorAll('[data-db-property]').forEach(button=>{button.disabled=true;});
      body.querySelectorAll('[data-table-row],[data-calendar-row]').forEach(button=>{
        const row=Number(button.dataset.tableRow??button.dataset.calendarRow);
        const id=String(projected.props?.rows?.[row]?._canonical_row_id||'');
        button.dataset.linkedRowId=id;button.draggable=false;
        button.title='사이드 보기에서 열기';button.setAttribute('aria-label','사이드 보기에서 열기');
        button.onclick=()=>{if(host.current(scope)){const menu=button.closest('[data-undated]');if(menu)menu.open=false;host.inspect(scope,slot.id,projected,data,id,button);}};
      });
      body.querySelectorAll('select:not([data-db-field]):not([data-calendar-property]):not([data-timeline-property]):not([data-timeline-scale]),[data-db-file-input],[data-db-file-remove],[data-db-cell-button],[data-db-date-reminder],[data-db-date-end]').forEach(control=>{control.disabled=true;});
      if(!cellEditable)body.querySelectorAll('[data-db-field]').forEach(control=>{control.disabled=true;});
      function changeDateView(patch,localAllowed=false){
        if(!host.current(scope)||!slot.node.isConnected)return;
        const next=host.dates?.withViewConfig(slot.block,patch);if(!next)return;
        if(editable)viewChange(slot,next);else if(localAllowed){slot.block=next;display(slot,data);}
      }
      for(const type of ['calendar','timeline']){
        body.querySelectorAll(`[data-${type}-nav],[data-${type}-today]`).forEach(button=>{
          button.disabled=false;button.onclick=()=>changeDateView(host.dates.navigate(projected.props,type,button.hasAttribute(`data-${type}-today`)?'today':button.getAttribute(`data-${type}-nav`)),true);
        });
        body.querySelectorAll(`[data-${type}-property]`).forEach(select=>{select.disabled=!editable;select.onchange=()=>changeDateView({[`${type}_date_property`]:select.value});});
      }
      body.querySelectorAll('[data-timeline-scale]').forEach(select=>{select.disabled=false;select.onchange=()=>changeDateView({timeline_scale:select.value},true);});
      host.bindUndated?.(body);
      body.querySelectorAll('[data-undated-assign]').forEach(button=>{
        button.disabled=!cellEditable;
        button.onclick=async()=>{
          if(!host.current(scope)||!slot.node.isConnected||!host.canEditCells?.(scope,data)||host.cellsBusy?.(scope,slot.id))return;
          const menu=button.closest?.('[data-undated]'),reopen=Boolean(menu?.open),search=menu?.querySelector?.('[data-undated-search]')?.value||'';
          const hourScroll=projected.props?.timeline_scale==='hour'&&button.dataset.undatedView==='timeline'
            ?button.closest?.('.timeline-block')?.querySelector?.('.timeline-scroll'):null;
          const hourContext=hourScroll?{hourAnchor:hourScroll.dataset?.timelineStart||host.dates.timeline(projected.props).start,
            hourVisibleSlot:Math.ceil(hourScroll.scrollLeft/72)}:{};
          const cell=host.dates.assignment(projected,Number(button.dataset.undatedAssign),button.dataset.undatedField,new Date(),button.dataset.undatedView||projected.type,hourContext);if(!cell?.row_id)return;
          button.disabled=true;
          try{
            await host.assignDate(scope,slot.id,data,cell);
            if(reopen&&host.current(scope)&&slot.node.isConnected){
              const refreshed=slot.node.querySelector('[data-undated]');
              if(refreshed){refreshed.open=true;const input=refreshed.querySelector('[data-undated-search]');if(input&&search){input.value=search;input.dispatchEvent(new Event('input',{bubbles:true}));}}
            }
          }catch(error){host.error?.(error);}finally{if(button.isConnected)button.disabled=!host.canEditCells?.(scope,data);}
        };
      });
      slot.node.querySelectorAll('[data-linked-source]').forEach(button=>{
        if(!data.source?.document_id){
          if(button.classList.contains('linked-source')){
            const label=document.createElement('span');label.className='linked-source';
            label.textContent=data.database.title||'데이터베이스';button.replaceWith(label);
          }else button.remove();
          return;
        }
        button.onclick=()=>{if(host.current(scope))host.open(data.source.document_id);};
      });
      slot.node.querySelector('[data-linked-refresh]').onclick=()=>refresh(true);
      const searchButton=slot.node.querySelector('[data-linked-toolbar-search]');
      const searchInput=slot.node.querySelector('[data-linked-search]');
      searchButton?.addEventListener('click',()=>{
        slot.searchOpen=!searchOpen;
        if(!slot.searchOpen)host.searchChange?.(scope,projected,'');
        display(slot,data);
        if(slot.searchOpen)slot.node.querySelector('[data-linked-search]')?.focus({preventScroll:true});
        else slot.node.querySelector('[data-linked-toolbar-search]')?.focus({preventScroll:true});
      });
      searchInput?.addEventListener('input',()=>{
        const value=searchInput.value,start=searchInput.selectionStart,end=searchInput.selectionEnd;
        slot.searchOpen=true;
        host.searchChange?.(scope,projected,value);
        display(slot,data);
        const restored=slot.node.querySelector('[data-linked-search]');
        restored?.focus({preventScroll:true});
        if(restored?.setSelectionRange&&start!=null)restored.setSelectionRange(start,end);
      });
      searchInput?.addEventListener('keydown',event=>{
        if(event.key!=='Escape'||event.isComposing||event.keyCode===229)return;
        event.preventDefault();event.stopPropagation();
        slot.searchOpen=false;host.searchChange?.(scope,projected,'');
        display(slot,data);
        slot.node.querySelector('[data-linked-toolbar-search]')?.focus({preventScroll:true});
      });
      slot.node.querySelector('[data-linked-full-page]')?.addEventListener('click',event=>{
        if(event.currentTarget.tagName==='A'){
          if(event.metaKey||event.ctrlKey||event.shiftKey||event.altKey)return;
          event.preventDefault();
        }
        if(host.current(scope)&&slot.node.isConnected)host.toggleFullPage?.(scope,slot.id);
      });
      const createRow=options=>{
        if(!host.current(scope)||!slot.node.isConnected||!host.canCreateRows?.(scope,data))return;
        void Promise.resolve(host.createRow?.(scope,slot.id,data,options)).catch(error=>host.error?.(error));
      };
      slot.node.querySelector('[data-linked-row-create]')?.addEventListener('click',()=>createRow());
      slot.node.querySelector('[data-linked-row-create-blank]')?.addEventListener('click',()=>createRow({blank:true}));
      const templatePicker=slot.node.querySelector('[data-linked-template-picker]');
      const templateMenu=slot.node.querySelector('[data-linked-template-menu]');
      if(templatePicker&&templateMenu){
        templatePicker.addEventListener('click',()=>{
          if(templateMenu.matches(':popover-open')){templateMenuState=null;templateMenu.hidePopover();return;}
          templateMenu.showPopover();
          templateMenuState={pageId:scope.pageId,blockId:slot.id,
            sourceId:data.source?.database_id};
          const anchor=templatePicker.getBoundingClientRect();
          templateMenu.style.left=`${Math.max(8,Math.min(anchor.right-templateMenu.offsetWidth,innerWidth-templateMenu.offsetWidth-8))}px`;
          templateMenu.style.top=`${Math.max(8,Math.min(anchor.bottom+4,innerHeight-templateMenu.offsetHeight-8))}px`;
          templateMenu.querySelector('[data-linked-template-id]:not(:disabled)')?.focus({preventScroll:true});
        });
        templateMenu.addEventListener('toggle',()=>{
          const open=templateMenu.matches(':popover-open');
          templatePicker.setAttribute('aria-expanded',String(open));
          if(!open&&templateMenu.isConnected&&templateMenuState?.blockId===slot.id)
            templateMenuState=null;
        });
        templateMenu.querySelectorAll('[data-linked-template-id]').forEach(button=>
          button.addEventListener('click',()=>{
            const templateId=button.dataset.linkedTemplateId;
            templateMenu.hidePopover();
            createRow(templateId?{templateId}:{blank:true});
          }));
        templateMenu.querySelector('[data-linked-template-create]')?.addEventListener('click',()=>{
          templateMenu.hidePopover();
          if(host.current(scope)&&slot.node.isConnected)
            void Promise.resolve(host.createTemplate?.(scope,slot.id,data))
              .catch(error=>host.error?.(error));
        });
        templateMenu.querySelectorAll('[data-linked-template-edit]').forEach(button=>
          button.addEventListener('click',()=>{
            templateMenu.hidePopover();
            if(host.current(scope)&&slot.node.isConnected)
              void Promise.resolve(host.editTemplate?.(scope,slot.id,data,button.dataset.linkedTemplateEdit))
                .catch(error=>host.error?.(error));
          }));
      }
      bindViewMenu(slot,data,projected);
      slot.node.querySelector('[data-linked-add]')?.addEventListener('click',()=>settings(slot,data,'',true));
      slot.node.querySelectorAll('[data-linked-view]').forEach(button=>button.onclick=()=>{
        const next=clone(slot.block),view=next.props.views.find(v=>v.id===button.dataset.linkedView);if(!view)return;
        next.props.active_view_id=view.id;next.type=view.type;
        if(editable)viewChange(slot,next);else{slot.block=next;display(slot,data);}
      });
      slot.data=data;
      host.bindCells?.(scope,slot,projected,data,cellEditable);
      host.bindRows?.(scope,slot,data,rowEditable);
      host.bindTemplates?.(scope,slot,data);
      host.updated?.(scope,slot.id,projected,data);
    }
    async function refresh(force=false){
      if(!context||!host.current(context)){clear();return;}
      if(!host.visible()){pause();return;}
      if(flight){const pending=flight,scope=context;await pending;if(force&&context===scope)return refresh(true);return;}
      const version=generation,scope=context;abort=new AbortController();const signal=abort.signal;
      flight=(async()=>{let index=0;async function worker(){while(index<slots.length&&current(version,scope)&&!signal.aborted){
        const slot=slots[index++];if(!slot.node.isConnected||!host.visible(slot))continue;
        if(!force&&host.cellsBusy?.(scope,slot.id))continue;
        if(!slot.data)slot.node.innerHTML='<p class="linked-database-status" role="status">연결된 데이터베이스 불러오는 중…</p>';
        try{const data=await host.load(scope,slot.id,signal);if(!current(version,scope)||signal.aborted)return;
          // A forced refresh may finish while the template picker is open.
          // Preserve its live popover until the user chooses an action, but
          // repaint if edit access, its witness, or the linked source changed.
          const templateMenu=slot.node.querySelector('[data-linked-template-menu]');
          if(templateMenu?.matches?.(':popover-open')
              &&host.canCreateTemplates?.(scope,data)
              &&slot.data?.source?.database_id===data.source?.database_id
              &&JSON.stringify(slot.data?.access)===JSON.stringify(data.access)
              &&JSON.stringify(slot.data?.instance_witness)===JSON.stringify(data.instance_witness))continue;
          if(templateMenu&&templateMenuState?.blockId===slot.id)templateMenuState=null;
          const unchanged=slot.data&&JSON.stringify(slot.data)===JSON.stringify(data);
          if(unchanged&&(slot.relationPickerOpen||slot.closeFileMenu))continue;
          if(!force&&unchanged)continue;
          if(!force&&host.interacting?.(slot)&&(host.canEditCells?.(scope,data)
              ||slot.node.querySelector('[data-linked-search]')?.matches?.(':focus')))continue;
          display(slot,data);}
        catch(error){if(!current(version,scope)||signal.aborted)return;slot.data=null;const message=[401,403,404].includes(error?.status)?'원본에 접근할 수 없거나 보기가 아직 저장되지 않았습니다.':'연결된 데이터베이스를 불러오지 못했습니다.';host.unavailable?.(scope,slot.id,message);slot.node.innerHTML=`<p class="linked-database-status" role="status">${message}</p><button type="button" data-linked-retry>다시 불러오기</button>`;slot.node.querySelector('button').onclick=()=>refresh();}
      }}await Promise.all([worker(),worker()]);})();
      const task=flight;try{await task;}finally{if(flight===task){flight=null;abort=null;}}
    }
    async function mount(scope,nextSlots){
      const samePage=context?.pageId===scope?.pageId;
      const retained=samePage?menuState:null;
      const openTemplate=samePage?slots.find(item=>
        item.node.querySelector('[data-linked-template-menu]')?.matches?.(':popover-open')):null;
      const retainedTemplate=samePage?(openTemplate
        ?{pageId:scope.pageId,blockId:openTemplate.id,
          sourceId:openTemplate.data?.source?.database_id}:templateMenuState):null;
      dispose();context=scope;menuState=retained;templateMenuState=retainedTemplate;slots=nextSlots;
      host.mounted?.(scope,slots.map(slot=>slot.id));await refresh();
      if(context!==scope||!templateMenuState||templateMenuState.pageId!==scope.pageId)return;
      const live=slots.find(item=>item.id===templateMenuState.blockId);
      if(!live?.data||live.data.source?.database_id!==templateMenuState.sourceId
          ||!host.canCreateTemplates?.(scope,live.data)){
        templateMenuState=null;return;
      }
      const menu=live.node.querySelector('[data-linked-template-menu]');
      const picker=live.node.querySelector('[data-linked-template-picker]');
      if(!menu||!picker||menu.matches(':popover-open'))return;
      menu.showPopover();picker.setAttribute('aria-expanded','true');
      const anchor=picker.getBoundingClientRect();
      menu.style.left=`${Math.max(8,Math.min(anchor.right-menu.offsetWidth,innerWidth-menu.offsetWidth-8))}px`;
      menu.style.top=`${Math.max(8,Math.min(anchor.bottom+4,innerHeight-menu.offsetHeight-8))}px`;
      menu.querySelector('[data-linked-template-id]:not(:disabled)')?.focus({preventScroll:true});
    }
    function slot(blockId){return context&&host.current(context)?slots.find(item=>item.id===blockId)||null:null;}
    return {mount,refresh,clear,dispose,slot};
  }
  return {reference,descriptor,localView,viewMenuHtml,showViewMenu,positionViewMenu,filterViewProperties,filterViewGroups,filterViewSorts,filterViewFilters,filterValueHtml,filterMatchesRule,filterMatchesGroup,filterGroupRuleCount,validFilterGroup,filterGroupWithoutProperty,mountAdvancedFilterEditor,bindViewPropertyOrder,orderedProperties,movedPropertyOrder,sortGroupEntries,groupEntriesForRows,boardGroupEntriesForRows,movedGroupOrder,GROUP_SORTS,create,TYPES};
});

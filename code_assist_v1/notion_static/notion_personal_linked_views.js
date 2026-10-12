(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.NotionPersonalLinkedViews = api;
})(typeof globalThis === 'object' ? globalThis : this, function () {
  'use strict';
  const escape = value => String(value ?? '').replace(/[&<>"']/g, char => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  })[char]);
  const filterOperators = {
    contains:'포함',not_contains:'포함하지 않음',equals:'같음',not_equals:'같지 않음',
    greater_than:'보다 큼',less_than:'보다 작음',empty:'비어 있음',not_empty:'비어 있지 않음',
    on_or_before:'이전 또는 같음',on_or_after:'이후 또는 같음',
  };
  const viewTypes = {table:'표',board:'보드',list:'리스트',gallery:'갤러리',calendar:'캘린더',timeline:'타임라인'};
  const tableOptions = [['show_source_title','데이터 소스 제목 표시'],['show_vertical_lines','세로선 표시'],['show_page_icons','페이지 아이콘 표시'],['wrap_all_content','모든 콘텐츠 줄바꿈']];

  function create(host) {
    let scope = null, slots = [], generation = 0, abort = null, flight = null, menuState = null;
    const editableTypes = new Set(['title','text','number','checkbox','url','email','phone','place','select','status','multi_select']);
    const current = (version, context) => version === generation && scope === context && host.current(context);
    const activeView = slot => {
      const views = slot.block.props?.views || [];
      const id = host.canEdit(scope) ? slot.block.props?.active_view_id
        : slot.localViewId || slot.block.props?.active_view_id;
      return views.find(view => view.id === id) || views[0];
    };
    document.addEventListener('pointerdown', event => {
      const open = menuState;
      if (open?.picker?.isConnected && !open.picker.contains(event.target)
          && !open.trigger?.contains(event.target)) menuState = null;
    }, true);
    window.addEventListener('resize', () => {
      if (menuState?.picker?.matches(':popover-open'))
        globalThis.NotionLinkedDatabases.positionViewMenu(menuState.trigger,menuState.picker);
    });

    function clear() {
      generation += 1;
      menuState = null;
      abort?.abort();
      abort = null;
      flight = null;
      slots.forEach(slot => {
        slot.closeRules?.();
        slot.closeRelationPicker?.(false);
        slot.closeFileMenu?.(false,false);
      });
      scope = null;
      slots = [];
    }

    function openRules(slot, data, kind) {
      if (!host.current(scope) || !host.canEdit(scope)) return;
      if (slot.rulesOpen === kind) { slot.closeRules?.(true); return; }
      slot.closeRules?.();
      const view = slot.block.props?.views?.find(item => item.id === slot.block.props.active_view_id);
      const properties = (data.block.props?.properties || []).filter(item => item?.id);
      if (!view || !properties.length) return;
      let rules = structuredClone(Array.isArray(view.config?.[kind]) ? view.config[kind] : []);
      const panel = slot.node.querySelector('[data-personal-linked-rules]');
      const trigger = slot.node.querySelector(`[data-personal-linked-${kind}]`);
      const propertyOptions = selected => properties.map(item =>
        `<option value="${escape(item.id)}" ${item.id === selected ? 'selected' : ''}>${escape(item.name || item.id)}</option>`).join('');
      const collect = () => {
        rules = [...panel.querySelectorAll('[data-personal-linked-rule]')].map(row => kind === 'filters'
          ? {property:row.querySelector('[data-rule-property]').value,
            operator:row.querySelector('[data-rule-operator]').value,
            value:row.querySelector('[data-rule-value]').value}
          : {property:row.querySelector('[data-rule-property]').value,
            direction:row.querySelector('[data-rule-direction]').value});
      };
      const draw = () => {
        panel.innerHTML = `<div class="personal-linked-rules-heading"><strong>${kind === 'filters' ? '필터 · 모든 조건 일치' : '정렬'}</strong><button type="button" data-rule-close aria-label="닫기">×</button></div>
          <div class="personal-linked-rules-list">${rules.map((rule, index) => `<div class="personal-linked-rule" data-personal-linked-rule="${index}">
            <select data-rule-property aria-label="${kind === 'filters' ? '필터' : '정렬'} 속성">${propertyOptions(rule.property)}</select>
            ${kind === 'filters' ? `<select data-rule-operator aria-label="필터 조건">${Object.entries(filterOperators).map(([key,label]) => `<option value="${key}" ${rule.operator === key ? 'selected' : ''}>${label}</option>`).join('')}</select><input data-rule-value aria-label="필터 값" value="${escape(rule.value ?? '')}" placeholder="값">`
              : `<select data-rule-direction aria-label="정렬 방향"><option value="asc" ${rule.direction === 'desc' ? '' : 'selected'}>오름차순</option><option value="desc" ${rule.direction === 'desc' ? 'selected' : ''}>내림차순</option></select>`}
            <button type="button" data-rule-remove="${index}" aria-label="조건 삭제">×</button></div>`).join('')}</div>
          <div class="personal-linked-rules-footer"><button type="button" data-rule-add>＋ ${kind === 'filters' ? '필터' : '정렬'} 추가</button><button type="button" data-rule-clear>모두 지우기</button><button type="button" class="personal-linked-rule-apply" data-rule-apply>적용</button></div>`;
      };
      const outside = event => {
        if (!panel.contains(event.target) && !trigger.contains(event.target)) slot.closeRules?.();
      };
      slot.closeRules = (focus = false) => {
        document.removeEventListener('pointerdown', outside, true);
        panel.hidden = true;
        panel.replaceChildren();
        trigger.setAttribute('aria-expanded', 'false');
        slot.rulesOpen = null;
        slot.closeRules = null;
        if (focus && trigger.isConnected) trigger.focus({preventScroll:true});
      };
      slot.rulesOpen = kind;
      trigger.setAttribute('aria-expanded', 'true');
      panel.hidden = false;
      draw();
      panel.onclick = event => {
        const button = event.target.closest('button');
        if (!button) return;
        if (button.hasAttribute('data-rule-close')) { slot.closeRules?.(true); return; }
        if (button.hasAttribute('data-rule-add')) {
          collect();
          if (rules.length >= 16) return;
          rules.push(kind === 'filters'
            ? {property:properties[0].id,operator:'contains',value:''}
            : {property:properties[0].id,direction:'asc'});
          draw();
          panel.querySelector('[data-personal-linked-rule]:last-child [data-rule-property]')?.focus();
        } else if (button.hasAttribute('data-rule-remove')) {
          collect(); rules.splice(Number(button.dataset.ruleRemove), 1); draw();
        } else if (button.hasAttribute('data-rule-clear')) {
          rules = []; draw();
        } else if (button.hasAttribute('data-rule-apply')) {
          collect();
          if (!host.current(scope) || !host.canEdit(scope)) { slot.closeRules?.(); return; }
          if (host.updateView(scope, slot.block, data, view.id, kind, rules)) slot.closeRules?.();
        }
      };
      panel.onkeydown = event => {
        if (event.key === 'Escape' && !event.isComposing) {
          event.preventDefault(); event.stopPropagation(); slot.closeRules?.(true);
        }
      };
      document.addEventListener('pointerdown', outside, true);
      (panel.querySelector('[data-rule-property]') || panel.querySelector('[data-rule-add]'))?.focus();
    }

    function openAdvancedRules(slot, data) {
      if (!host.current(scope) || !host.canEdit(scope)) return;
      slot.closeRules?.();
      const view = activeView(slot);
      if (!view) return;
      const initial = view.config?.filter_group || {logic:'and',children:structuredClone(view.config?.filters||[])};
      let editor;
      const form = host.modal('고급 필터',
        '<p class="database-rules-note">AND/OR 조건 묶음은 3단계까지 만들 수 있습니다.</p><div class="advanced-filter-editor" data-advanced-filter-editor></div>', [
          {label:'필터 지우기',action:() => {
            const current = slots.find(item => item.id === slot.id);
            if (current && quickViewChange(current,current.data || data,{filter_group:null},'filter')) host.closeModal();
          }},
          {label:'취소',action:host.closeModal},
          {label:'적용',primary:true,action:() => {
            const current = slots.find(item => item.id === slot.id);
            if (current && editor && quickViewChange(current,current.data || data,
              {filter_group:editor.get()},'filter')) host.closeModal();
          }},
        ]);
      editor = globalThis.NotionLinkedDatabases.mountAdvancedFilterEditor(
        form.querySelector('[data-advanced-filter-editor]'),initial,data.block.props?.properties||[]);
      return form;
    }

    function openViewSettings(slot, data, add = false) {
      if (!host.current(scope) || !host.canEdit(scope)) return;
      slot.closeRules?.();
      const context = scope;
      const active = slot.block.props?.views?.find(view => view.id === slot.block.props.active_view_id);
      const views = slot.block.props?.views || [];
      if (!active || (add && views.length >= 64)) return;
      const properties = (data.block.props?.properties || []).filter(item => item?.id);
      const chosen = add ? {name:'표',type:'table',config:{}} : active;
      const config = chosen.config || {};
      const form = host.modal(add ? '새 연결 보기' : '연결 보기 설정',
        `<div class="linked-view-settings personal-linked-view-settings">
          <label>보기 이름<input data-personal-view-name maxlength="100" value="${escape(chosen.name)}"></label>
          <label>레이아웃<select data-personal-view-type>${Object.entries(viewTypes).map(([type,label]) =>
            `<option value="${type}" ${chosen.type === type ? 'selected' : ''}>${label}</option>`).join('')}</select></label>
          <label>그룹<select data-personal-view-group><option value="">없음</option>${properties.map(item =>
            `<option value="${escape(item.id)}" ${(chosen.type==='board'?config.board_group_property:config.group_property) === item.id ? 'selected' : ''}>${escape(item.name || item.id)}</option>`).join('')}</select></label>
          <label>그룹 정렬<select data-personal-view-group-sort>${globalThis.NotionLinkedDatabases.GROUP_SORTS.map(([mode,label])=>`<option value="${mode}" ${config.group_sort===mode||!config.group_sort&&mode==='manual'?'selected':''}>${label}</option>`).join('')}</select></label>
          <fieldset><legend>표시할 속성</legend>${properties.map(item => `<label><input type="checkbox" data-personal-view-visible="${escape(item.id)}" ${config.property_visibility?.[item.id] === false ? '' : 'checked'} ${item.type === 'title' ? 'disabled' : ''}>${escape(item.name || item.id)}</label>`).join('')}</fieldset>
          <fieldset data-personal-view-table-options><legend>표 레이아웃</legend>${tableOptions.map(([key,label]) => `<label class="database-view-display-option"><span>${label}</span><input type="checkbox" role="switch" data-personal-view-display="${key}" ${config[key] === false ? '' : 'checked'}></label>`).join('')}</fieldset>
        </div>`, [
          ...(!add ? [{label:'보기 복제',action:() => save('duplicate')},
            ...(views.length > 1 ? [{label:'보기 삭제',action:() => save('delete')}] : [])] : []),
          {label:'취소',action:host.closeModal},
          {label:add ? '보기 만들기' : '적용',primary:true,action:() => save(add ? 'add' : 'edit')},
        ]);
      function save(mode) {
        if (!host.current(context) || !form.isConnected) return;
        const checked = new Map([...form.querySelectorAll('[data-personal-view-visible]')]
          .map(input => [input.dataset.personalViewVisible, input.checked]));
        const selectedGroup=form.querySelector('[data-personal-view-group]').value;
        const board=form.querySelector('[data-personal-view-type]').value==='board';
        const previousGroup=board?config.board_group_property:config.group_property;
        const request = {mode,viewId:active.id,
          name:form.querySelector('[data-personal-view-name]').value.trim(),
          type:form.querySelector('[data-personal-view-type]').value,
          group_property:board?config.group_property||'':selectedGroup,
          board_group_property:board?selectedGroup:config.board_group_property||'',
          group_sort:form.querySelector('[data-personal-view-group-sort]').value,
          group_order:selectedGroup===(previousGroup||'')?config.group_order||[]:[],
          property_visibility:Object.fromEntries(properties.map(item => [item.id,
            item.type === 'title' || Boolean(checked.get(item.id))])),
          display:Object.fromEntries([...form.querySelectorAll('[data-personal-view-display]')]
            .map(input => [input.dataset.personalViewDisplay,input.checked])),
        };
        const currentSlot = slots.find(item => item.id === slot.id);
        if (host.commitView(context, currentSlot?.block || slot.block,
          currentSlot?.data || data, request)) host.closeModal();
        else host.viewError?.();
      }
      const showTableOptions = () => {
        const type=form.querySelector('[data-personal-view-type]').value;
        form.querySelector('[data-personal-view-table-options]').hidden = type !== 'table';
        const group=form.querySelector('[data-personal-view-group]');
        for(const option of group.options){
          const property=properties.find(item=>item.id===option.value);
          option.disabled=type==='board'&&Boolean(option.value)
            &&!['status','select','person','checkbox','multi_select'].includes(property?.type);
        }
        if(type==='board'&&group.selectedOptions[0]?.disabled)
          group.value=properties.find(item=>['status','select','person','checkbox','multi_select'].includes(item.type))?.id||'';
      };
      form.querySelector('[data-personal-view-type]').addEventListener('change',showTableOptions);
      showTableOptions();
      form.querySelector('[data-personal-view-name]')?.focus();
      return form;
    }

    function quickViewChange(slot, data, patch, panel = 'main', focusKey = null) {
      if (!host.current(scope) || !host.canEdit(scope)) return false;
      const view = activeView(slot);
      if (!view) return false;
      const properties = (data.block.props?.properties || []).filter(item => item?.id);
      const config = view.config || {};
      const display = Object.fromEntries(tableOptions.map(([key]) =>
        [key, config[key] !== false]));
      if (patch.display) Object.assign(display, patch.display);
      const request = {
        mode:'edit', viewId:view.id,
        name:patch.name ?? view.name, type:patch.type ?? view.type,
        group_property:patch.group_property ?? config.group_property ?? '',
        board_group_property:patch.board_group_property ?? config.board_group_property ?? '',
        group_sort:patch.group_sort ?? config.group_sort ?? 'manual',
        group_order:patch.group_order ?? config.group_order ?? [],
        property_visibility:patch.property_visibility || Object.fromEntries(properties.map(item =>
          [item.id, item.type === 'title' || config.property_visibility?.[item.id] !== false])),
        property_order:patch.property_order ?? config.property_order ?? [],
        display,
        ...(Object.hasOwn(patch,'filter_group')?{filter_group:patch.filter_group}:{}),
      };
      menuState = menuState?.blockId === slot.id && menuState.picker?.isConnected
        ? {pageId:scope.pageId, userId:scope.userId, blockId:slot.id, panel, focusKey,
          query:menuState.query || ''} : null;
      if (host.commitView(scope, slot.block, data, request)) return true;
      menuState = null;
      host.viewError?.();
      return false;
    }

    function bindViewMenu(slot, data) {
      const trigger = slot.node.querySelector('[data-personal-linked-view-settings]');
      const picker = slot.node.querySelector('[data-linked-view-popover]');
      if (!trigger || !picker) return;
      const show = (panel = 'main', focusKey = null,
        query = menuState?.panel === panel ? menuState.query || '' : '') => {
        menuState = {pageId:scope.pageId, userId:scope.userId, blockId:slot.id,
          panel, focusKey, query, trigger, picker};
        globalThis.NotionLinkedDatabases.showViewMenu(trigger, picker, panel);
        const search = picker.querySelector('[data-linked-pop-property-search]');
        if (search) search.value = panel === 'properties' ? query : '';
        globalThis.NotionLinkedDatabases.filterViewProperties(picker, panel === 'properties' ? query : '');
        const groupSearch = picker.querySelector('[data-linked-pop-group-search]');
        if (groupSearch) groupSearch.value = panel === 'group' ? query : '';
        globalThis.NotionLinkedDatabases.filterViewGroups(picker, panel === 'group' ? query : '');
        const sortSearch = picker.querySelector('[data-linked-pop-sort-search]');
        if (sortSearch) sortSearch.value = panel === 'sort' ? query : '';
        globalThis.NotionLinkedDatabases.filterViewSorts(picker, panel === 'sort' ? query : '');
        const filterSearch = picker.querySelector('[data-linked-pop-filter-search]');
        if (filterSearch) filterSearch.value = panel === 'filter' ? query : '';
        globalThis.NotionLinkedDatabases.filterViewFilters(picker, panel === 'filter' ? query : '');
        if (focusKey?.startsWith('property:'))
          picker.querySelector(`[data-linked-pop-visible="${CSS.escape(focusKey.slice(9))}"]`)?.focus({preventScroll:true});
        else if (focusKey?.startsWith('order:'))
          picker.querySelector(`[data-view-property-id="${CSS.escape(focusKey.slice(6))}"] .database-view-property-handle`)?.focus({preventScroll:true});
        else if (focusKey?.startsWith('group:'))
          picker.querySelector(`[data-linked-pop-group-choice="${CSS.escape(focusKey.slice(6))}"]`)?.focus({preventScroll:true});
        else if (focusKey === 'group-sort') picker.querySelector('[data-linked-pop-group-sort]')?.focus({preventScroll:true});
        else if (focusKey?.startsWith('group-move:'))
          picker.querySelector(`[data-linked-pop-group-move="${CSS.escape(focusKey.slice(11))}"]`)?.focus({preventScroll:true});
        else if (focusKey?.startsWith('sort-direction:'))
          picker.querySelector(`[data-linked-pop-sort-direction="${CSS.escape(focusKey.slice(15))}"]`)?.focus({preventScroll:true});
        else if (focusKey === 'sort-search') sortSearch?.focus({preventScroll:true});
        else if (focusKey?.startsWith('filter-value:'))
          picker.querySelector(`[data-linked-pop-filter-value="${CSS.escape(focusKey.slice(13))}"]`)?.focus({preventScroll:true});
        else if (focusKey?.startsWith('filter-operator:'))
          picker.querySelector(`[data-linked-pop-filter-operator="${CSS.escape(focusKey.slice(16))}"]`)?.focus({preventScroll:true});
        else if (focusKey === 'filter-search') filterSearch?.focus({preventScroll:true});
        else if (focusKey === 'all-hide')
          picker.querySelector('[data-linked-pop-hide-all]')?.focus({preventScroll:true});
        else if (focusKey && focusKey !== 'name')
          picker.querySelector(`[data-linked-pop-display="${focusKey}"]`)?.focus({preventScroll:true});
      };
      const close = (focus = false) => {
        menuState = null;
        if (picker.matches(':popover-open')) picker.hidePopover();
        if (focus && trigger.isConnected) trigger.focus({preventScroll:true});
      };
      trigger.addEventListener('click', () => picker.matches(':popover-open') ? close() : show());
      picker.addEventListener('toggle', event => {
        trigger.setAttribute('aria-expanded', String(event.newState === 'open'));
        if (event.newState === 'closed' && picker.isConnected && menuState?.picker === picker)
          menuState = null;
      });
      picker.querySelectorAll('[data-linked-pop-close]').forEach(button =>
        button.addEventListener('click', () => close(true)));
      picker.querySelector('[data-linked-pop-layout]')?.addEventListener('click', () => show('layout'));
      picker.querySelector('[data-linked-pop-properties]')?.addEventListener('click', () => show('properties'));
      picker.querySelector('[data-linked-pop-group]')?.addEventListener('click', () => show('group'));
      picker.querySelector('[data-linked-pop-sort]')?.addEventListener('click', () => show('sort'));
      picker.querySelector('[data-linked-pop-filter]')?.addEventListener('click', () => show('filter'));
      picker.querySelectorAll('[data-linked-pop-back]').forEach(button =>
        button.addEventListener('click', () => show()));
      picker.addEventListener('keydown', event => {
        if (event.key !== 'Escape' || event.isComposing) return;
        if (!picker.querySelector('[data-linked-view-main]').hidden) { menuState = null; return; }
        event.preventDefault(); event.stopPropagation(); show();
      });
      picker.querySelector('[data-linked-pop-property-search]')?.addEventListener('input', event => {
        if (menuState?.picker === picker) menuState.query = event.target.value;
        globalThis.NotionLinkedDatabases.filterViewProperties(picker, event.target.value);
      });
      picker.querySelector('[data-linked-pop-group-search]')?.addEventListener('input', event => {
        if (menuState?.picker === picker) menuState.query = event.target.value;
        globalThis.NotionLinkedDatabases.filterViewGroups(picker, event.target.value);
      });
      picker.querySelector('[data-linked-pop-sort-search]')?.addEventListener('input', event => {
        if (menuState?.picker === picker) menuState.query = event.target.value;
        globalThis.NotionLinkedDatabases.filterViewSorts(picker, event.target.value);
      });
      picker.querySelector('[data-linked-pop-filter-search]')?.addEventListener('input', event => {
        if (menuState?.picker === picker) menuState.query = event.target.value;
        globalThis.NotionLinkedDatabases.filterViewFilters(picker, event.target.value);
      });
      const activeFilters = () => Array.isArray(activeView(slot)?.config?.filters)
        ? activeView(slot).config.filters : [];
      const changeFilters = (filters, focusKey) => {
        const view = activeView(slot);
        if (!view) return;
        menuState = menuState?.blockId === slot.id && menuState.picker?.isConnected
          ? {pageId:scope.pageId, userId:scope.userId, blockId:slot.id,
            panel:'filter', focusKey, query:menuState.query || ''} : null;
        if (!host.updateView(scope, slot.block, data, view.id, 'filters', filters)) menuState = null;
      };
      picker.querySelectorAll('[data-linked-pop-filter-choice]').forEach(button => button.addEventListener('click', () => {
        const property = button.dataset.linkedPopFilterChoice;
        if (!(data.block.props?.properties || []).some(item => item.id === property)) return;
        const filters = activeFilters();
        if (filters.length >= 16) return;
        changeFilters([...filters, {property, operator:'contains', value:''}], `filter-value:${filters.length}`);
      }));
      picker.querySelectorAll('[data-linked-pop-filter-operator]').forEach(input => input.addEventListener('change', () => {
        const index = Number(input.dataset.linkedPopFilterOperator), filters = activeFilters();
        if (!Number.isInteger(index) || index < 0 || index >= filters.length
            || !Object.hasOwn(filterOperators, input.value)) return;
        changeFilters(filters.map((rule, i) => i === index ? {...rule, operator:input.value} : rule),
          `filter-operator:${index}`);
      }));
      picker.querySelectorAll('[data-linked-pop-filter-value]').forEach(input => input.addEventListener('change', () => {
        const index = Number(input.dataset.linkedPopFilterValue), filters = activeFilters();
        if (!Number.isInteger(index) || index < 0 || index >= filters.length) return;
        changeFilters(filters.map((rule, i) => i === index ? {...rule, value:input.value} : rule),
          `filter-value:${index}`);
      }));
      picker.querySelectorAll('[data-linked-pop-filter-remove]').forEach(button => button.addEventListener('click', () => {
        const index = Number(button.dataset.linkedPopFilterRemove), filters = activeFilters();
        if (!Number.isInteger(index) || index < 0 || index >= filters.length) return;
        changeFilters(filters.filter((_, i) => i !== index), 'filter-search');
      }));
      const activeSorts = () => Array.isArray(activeView(slot)?.config?.sorts)
        ? activeView(slot).config.sorts : [];
      const changeSorts = (sorts, focusKey) => {
        const view = activeView(slot);
        if (!view) return;
        menuState = menuState?.blockId === slot.id && menuState.picker?.isConnected
          ? {pageId:scope.pageId, userId:scope.userId, blockId:slot.id,
            panel:'sort', focusKey, query:menuState.query || ''} : null;
        if (!host.updateView(scope, slot.block, data, view.id, 'sorts', sorts)) menuState = null;
      };
      picker.querySelectorAll('[data-linked-pop-sort-choice]').forEach(button => button.addEventListener('click', () => {
        const property = button.dataset.linkedPopSortChoice;
        if (!(data.block.props?.properties || []).some(item => item.id === property)) return;
        const sorts = activeSorts();
        if (sorts.length >= 16 || sorts.some(rule => rule.property === property)) return;
        changeSorts([...sorts, {property, direction:'asc'}], 'sort-search');
      }));
      picker.querySelectorAll('[data-linked-pop-sort-direction]').forEach(input => input.addEventListener('change', () => {
        const index = Number(input.dataset.linkedPopSortDirection), sorts = activeSorts();
        if (!Number.isInteger(index) || index < 0 || index >= sorts.length) return;
        changeSorts(sorts.map((rule, i) => i === index ? {...rule, direction:input.value} : rule),
          `sort-direction:${index}`);
      }));
      picker.querySelectorAll('[data-linked-pop-sort-remove]').forEach(button => button.addEventListener('click', () => {
        const index = Number(button.dataset.linkedPopSortRemove), sorts = activeSorts();
        if (!Number.isInteger(index) || index < 0 || index >= sorts.length) return;
        changeSorts(sorts.filter((_, i) => i !== index), 'sort-search');
      }));
      picker.querySelectorAll('[data-linked-pop-group-choice]').forEach(button => button.addEventListener('click', () => {
        const value = button.dataset.linkedPopGroupChoice;
        const view=activeView(slot),board=view?.type==='board';
        const properties=(data.block.props?.properties||[]).filter(item=>!board
          ||['status','select','person','checkbox','multi_select'].includes(item.type));
        if(board&&!value||value&&!properties.some(item=>item.id===value))return;
        const key=board?'board_group_property':'group_property';
        const current=board?(view?.config?.board_group_property||properties[0]?.id||''):(view?.config?.group_property||'');
        if(current!==value)quickViewChange(slot,data,{[key]:value,group_order:[]},'group',`group:${value}`);
      }));
      picker.querySelector('[data-linked-pop-group-sort]')?.addEventListener('change', event => {
        const mode = event.target.value;
        if (globalThis.NotionLinkedDatabases.GROUP_SORTS.some(([key])=>key===mode))
          quickViewChange(slot,data,{group_sort:mode},'group','group-sort');
      });
      picker.querySelectorAll('[data-linked-pop-group-move]').forEach(button=>button.addEventListener('click',()=>{
        const view=activeView(slot);
        const properties=data.block.props?.properties||[];
        const entries=view?.type==='board'
          ?globalThis.NotionLinkedDatabases.boardGroupEntriesForRows(data.block.props?.rows||[],properties,view.config).entries
          :globalThis.NotionLinkedDatabases.groupEntriesForRows(data.block.props?.rows||[],
            properties.find(item=>item.id===view?.config?.group_property));
        const order=globalThis.NotionLinkedDatabases.movedGroupOrder(entries,'manual',view?.config?.group_order,
          button.dataset.linkedPopGroupMove,Number(button.dataset.groupDirection));
        if(order)quickViewChange(slot,data,{group_order:order},'group',`group-move:${button.dataset.linkedPopGroupMove}`);
      }));
      const propertyVisibility = () => Object.fromEntries((data.block.props?.properties || [])
        .filter(item => item?.id).map(item => [item.id,
          item.type === 'title' || activeView(slot)?.config?.property_visibility?.[item.id] !== false]));
      picker.querySelectorAll('[data-linked-pop-visible]').forEach(button => button.addEventListener('click', () => {
        const id = button.dataset.linkedPopVisible;
        const visibility = propertyVisibility();
        if (!Object.hasOwn(visibility, id) || button.disabled) return;
        visibility[id] = !visibility[id];
        quickViewChange(slot, data, {property_visibility:visibility}, 'properties', `property:${id}`);
      }));
      picker.querySelector('[data-linked-pop-hide-all]')?.addEventListener('click', () => {
        const visibility = Object.fromEntries((data.block.props?.properties || []).filter(item => item?.id)
          .map(item => [item.id, item.type === 'title']));
        quickViewChange(slot, data, {property_visibility:visibility}, 'properties', 'all-hide');
      });
      globalThis.NotionLinkedDatabases.bindViewPropertyOrder(picker, (source, target) => {
        const view = activeView(slot);
        const order = globalThis.NotionLinkedDatabases.movedPropertyOrder(
          data.block.props?.properties || [], view?.config?.property_order, source, target);
        if (order) quickViewChange(slot, data, {property_order:order}, 'properties', `order:${source}`);
      });
      picker.querySelector('[data-linked-pop-name]')?.addEventListener('change', event => {
        const name = event.target.value.trim() || viewTypes[activeView(slot)?.type] || '표';
        event.target.value = name;
        if (name !== activeView(slot)?.name) quickViewChange(slot, data, {name}, 'main', 'name');
      });
      picker.querySelectorAll('[data-linked-pop-type]').forEach(button => button.addEventListener('click', () => {
        const type = button.dataset.linkedPopType;
        if (type !== activeView(slot)?.type) quickViewChange(slot, data, {type}, 'layout');
      }));
      picker.querySelectorAll('[data-linked-pop-display]').forEach(input => input.addEventListener('change', () => {
        quickViewChange(slot, data, {display:{[input.dataset.linkedPopDisplay]:input.checked}},
          'layout', input.dataset.linkedPopDisplay);
      }));
      for (const [selector, focus] of [
        ['[data-linked-pop-manage]', '[data-personal-view-name]'],
      ]) picker.querySelector(selector)?.addEventListener('click', () => {
        close();
        openViewSettings(slot, data)?.querySelector(focus)?.focus({preventScroll:true});
      });
      picker.querySelector('[data-linked-pop-filter-advanced]')?.addEventListener('click', () => {
        close();openAdvancedRules(slot,data)?.querySelector('[data-advanced-add-rule]')?.focus({preventScroll:true});
      });
      if (menuState?.pageId === scope.pageId && menuState.userId === scope.userId
          && menuState.blockId === slot.id) {
        const restore = {...menuState};
        requestAnimationFrame(() => {
          if (!host.current(scope) || !picker.isConnected || menuState?.blockId !== slot.id) return;
          show(restore.panel, restore.focusKey, restore.query);
          if (restore.focusKey === 'name')
            picker.querySelector('[data-linked-pop-name]')?.focus({preventScroll:true});
        });
      }
    }

    function renderPreview(slot, data) {
      const active = activeView(slot);
      const preview = host.project(slot.block, data.block, active);
      slot.node.querySelector('.personal-linked-preview').innerHTML = host.render(preview, slot.query || '', data.editable);
    }

    function bindFileCells(slot, data, body) {
      const rowAt = index => (slot.data || data).block?.props?.rows?.[index];
      const rowKey = (index, field) => {
        const row = rowAt(index);
        return JSON.stringify([String(row?._canonical_row_id || row?.page_id || row?.id || index),field]);
      };
      const filesAt = (index, field) => {
        const value = rowAt(index)?.[field];
        return Array.isArray(value) ? value : [];
      };
      const change = async (operation,onError) => {
        if (!host.current(scope) || slot.saving) return;
        slot.saving = true;
        try { await operation(); }
        catch (error) { onError?.(error); host.error(error); }
        finally { slot.saving = false; await refresh(true); }
      };
      body.querySelectorAll('.database-files-property').forEach(cell => {
        const toggle = cell.querySelector('[data-db-file-link-toggle]');
        if (!toggle) return;
        const upload = cell.querySelector('.database-file-add');
        if (!upload) return;
        const index = Number(toggle.dataset.dbRow), field = toggle.dataset.dbFileLinkToggle;
        if (!Number.isSafeInteger(index) || index < 0) return;
        const key = rowKey(index, field), form = cell.querySelector('[data-db-file-link-form]');
        const url = form?.querySelector('[data-db-file-link-url]');
        const name = form?.querySelector('[data-db-file-link-name]');
        const apply = form?.querySelector('[data-db-file-link-apply]');
        const cancel = form?.querySelector('[data-db-file-link-cancel]');
        if (!form || !url || !name || !apply || !cancel) return;
        const menuToggle=document.createElement('button');
        menuToggle.type='button';
        menuToggle.className='personal-linked-file-menu-toggle';
        menuToggle.textContent='＋ 파일 추가';
        menuToggle.setAttribute('aria-label','파일 추가');
        menuToggle.setAttribute('aria-haspopup','dialog');
        menuToggle.setAttribute('aria-expanded','false');
        const menu=document.createElement('div');
        menu.className='personal-linked-file-menu';
        menu.setAttribute('role','dialog');
        menu.setAttribute('aria-label','파일 추가');
        menu.hidden=true;
        upload.childNodes.forEach(node=>{if(node.nodeType===Node.TEXT_NODE) node.textContent='업로드';});
        upload.tabIndex=0;
        upload.setAttribute('role','button');
        upload.setAttribute('aria-label','업로드');
        upload.addEventListener('keydown',event=>{
          if (event.key==='Enter' || event.key===' ') {
            event.preventDefault();
            upload.querySelector('input[type="file"]')?.click();
          }
        });
        toggle.textContent='링크 임베드';
        cell.append(menuToggle,menu);
        menu.append(upload,toggle,form);
        const position=()=>{
          const rect=menuToggle.getBoundingClientRect();
          const width=Math.min(320,window.innerWidth-16);
          menu.style.width=`${width}px`;
          menu.style.left=`${Math.max(8,Math.min(rect.left,window.innerWidth-width-8))}px`;
          const height=Math.min(menu.offsetHeight||150,window.innerHeight-16);
          const below=rect.bottom+4;
          menu.style.top=`${Math.max(8,below+height>window.innerHeight-8
            ? Math.min(rect.top-height-4,window.innerHeight-height-8) : below)}px`;
        };
        const closeMenu=(restore=false,discard=true)=>{
          document.removeEventListener('pointerdown',outside,true);
          window.removeEventListener('resize',position);
          window.removeEventListener('scroll',position,true);
          menu.hidden=true;
          menuToggle.setAttribute('aria-expanded','false');
          if (discard) {
            form.hidden=true;
            toggle.setAttribute('aria-expanded','false');
            slot.fileDrafts?.delete(key);
          }
          if (slot.closeFileMenu===closeMenu) slot.closeFileMenu=null;
          if (restore && menuToggle.isConnected) menuToggle.focus({preventScroll:true});
        };
        const outside=event=>{
          if (!menu.contains(event.target) && !menuToggle.contains(event.target))
            closeMenu(false,true);
        };
        const openMenu=focus=>{
          slot.closeFileMenu?.(false,true);
          slot.closeFileMenu=closeMenu;
          menu.hidden=false;
          menuToggle.setAttribute('aria-expanded','true');
          document.addEventListener('pointerdown',outside,true);
          window.addEventListener('resize',position);
          window.addEventListener('scroll',position,true);
          position();
          if (focus) upload.focus({preventScroll:true});
        };
        menuToggle.onclick=()=>menu.hidden ? openMenu(true) : closeMenu(true,true);
        menu.addEventListener('keydown',event=>{
          if (event.key==='Escape' && !event.isComposing && form.hidden) {
            event.preventDefault();
            event.stopPropagation();
            closeMenu(true,true);
          }
        });
        form.dataset.personalFileKey = key;
        for (const control of cell.querySelectorAll('button,input')) control.disabled = false;
        const saved = slot.fileDrafts?.get(key);
        if (saved) {
          form.hidden = false;
          toggle.setAttribute('aria-expanded','true');
          url.value = saved.url;
          name.value = saved.name;
          if (saved.error) {
            const error=form.querySelector('[data-db-file-link-error]');
            error.textContent=saved.error;error.hidden=false;
          }
          openMenu(false);
        }
        const remember = () => {
          const error=form.querySelector('[data-db-file-link-error]');
          error.textContent='';error.hidden=true;
          slot.fileDrafts ||= new Map();
          slot.fileDrafts.set(key,{url:url.value,name:name.value});
        };
        const showError = error => {
          const message=error?.message||String(error);
          const notice=form.querySelector('[data-db-file-link-error]');
          notice.textContent=message;notice.hidden=false;
          slot.fileDrafts ||= new Map();
          slot.fileDrafts.set(key,{url:url.value,name:name.value,error:message});
          position();
        };
        for (const [input,isName] of [[url,false],[name,true]]) {
          const keepFocus = () => {
            slot.fileFocus={key,name:isName,start:input.selectionStart,end:input.selectionEnd};
          };
          input.addEventListener('focus',keepFocus);
          input.addEventListener('keyup',keepFocus);
          input.addEventListener('mouseup',keepFocus);
          input.addEventListener('blur',() => queueMicrotask(() => {
            if (input.isConnected && document.activeElement!==input
                && slot.fileFocus?.key===key && slot.fileFocus.name===isName)
              slot.fileFocus=null;
          }));
        }
        const close = () => {
          form.hidden = true;
          slot.fileDrafts?.delete(key);
          toggle.setAttribute('aria-expanded','false');
          toggle.focus({preventScroll:true});
        };
        toggle.onclick = () => {
          form.hidden = !form.hidden;
          toggle.setAttribute('aria-expanded',String(!form.hidden));
          if (form.hidden) slot.fileDrafts?.delete(key);
          else { remember(); url.focus(); }
        };
        form.addEventListener('input',remember);
        form.addEventListener('keydown',event => {
          if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); closeMenu(true,true); }
          else if (event.key === 'Enter') { event.preventDefault(); apply.click(); }
        });
        cancel.onclick = close;
        apply.onclick = () => {
          let link;
          try { link = host.cleanFileLink(url.value,name.value); }
          catch (error) { showError(error);return; }
          const current = filesAt(index,field);
          if (current.length >= 20) {
            const error=Error('파일은 최대 20개까지 추가할 수 있습니다.');
            showError(error);return;
          }
          void change(async () => {
            await host.commitCell(scope,slot.data || data,
              {row:index,field,value:[...current,link]});
            slot.fileDrafts?.delete(key);
          },showError);
        };
      });
      body.querySelectorAll('[data-db-file-remove]').forEach(button => {
        button.disabled = false;
        button.onclick = () => {
          const row=Number(button.dataset.dbRow), field=button.dataset.dbFileRemove;
          const fileIndex=Number(button.dataset.fileIndex), current=filesAt(row,field);
          if (!Number.isSafeInteger(fileIndex) || fileIndex < 0 || fileIndex >= current.length) return;
          void change(() => host.commitCell(scope,slot.data || data,
            {row,field,value:current.filter((_,index)=>index!==fileIndex)}));
        };
      });
      body.querySelectorAll('[data-db-file-input]').forEach(input => {
        input.disabled = false;
        input.onchange = () => {
          const files=[...(input.files||[])];
          if (!files.length) return;
          void change(() => host.uploadFiles(scope,slot.data || data,
            {row:Number(input.dataset.dbRow),field:input.dataset.dbFileInput},files));
        };
      });
      const move = (row,field,from,to) => {
        const current=filesAt(row,field);
        if (!Number.isSafeInteger(from) || !Number.isSafeInteger(to)
            || from<0 || to<0 || from>=current.length || to>=current.length || from===to) return;
        const next=current.slice(),[item]=next.splice(from,1);
        next.splice(to,0,item);
        void change(() => host.commitCell(scope,slot.data || data,{row,field,value:next}));
      };
      let dragging = null;
      const clearDrag = () => {
        dragging = null;
        body.querySelectorAll('.database-file-chip.file-drop-target')
          .forEach(chip => chip.classList.remove('file-drop-target'));
      };
      body.querySelectorAll('[data-db-file-drag]').forEach(handle => {
        handle.disabled = false;
        handle.ondragstart = event => {
          if (slot.saving || !host.current(scope)) { event.preventDefault(); return; }
          dragging = {row:Number(handle.dataset.dbRow),field:handle.dataset.dbFileDrag,
            from:Number(handle.dataset.fileIndex)};
          event.dataTransfer.effectAllowed = 'move';
          event.dataTransfer.setData('text/plain','personal-linked-file-reorder');
        };
        handle.ondragend = clearDrag;
        handle.onkeydown = event => {
          if (!event.altKey || !['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(event.key)) return;
          event.preventDefault();event.stopPropagation();
          const from=Number(handle.dataset.fileIndex);
          move(Number(handle.dataset.dbRow),handle.dataset.dbFileDrag,from,
            from + (['ArrowRight','ArrowDown'].includes(event.key) ? 1 : -1));
        };
      });
      body.querySelectorAll('[data-db-file-target]').forEach(chip => {
        chip.ondragover = event => {
          if (!dragging || dragging.row!==Number(chip.dataset.dbRow)
              || dragging.field!==chip.dataset.dbFileTarget) return;
          event.preventDefault();event.dataTransfer.dropEffect='move';
          body.querySelectorAll('.database-file-chip.file-drop-target')
            .forEach(item => item.classList.remove('file-drop-target'));
          if (dragging.from!==Number(chip.dataset.fileIndex)) chip.classList.add('file-drop-target');
        };
        chip.ondrop = event => {
          if (!dragging || dragging.row!==Number(chip.dataset.dbRow)
              || dragging.field!==chip.dataset.dbFileTarget) return;
          event.preventDefault();event.stopPropagation();
          const {row,field,from}=dragging,to=Number(chip.dataset.fileIndex);
          clearDrag();move(row,field,from,to);
        };
      });
    }

    function bindRelationCells(slot, data, body) {
      const properties=(data.block.props?.properties||[]).filter(item=>item?.type==='relation' && item.id);
      for (const property of properties) {
        const controls=[...body.querySelectorAll('select[data-db-row][data-db-field]')]
          .filter(select=>select.dataset.dbField===property.id);
        if (!controls.length) continue;
        for (const select of controls) {
          select.disabled=true;
          select.hidden=true;
          select.tabIndex=-1;
          select.setAttribute('aria-hidden','true');
          select.replaceChildren(new Option('대상 불러오는 중…',''));
          const wrapper=document.createElement('span');
          wrapper.className='personal-linked-relation-cell';
          select.before(wrapper);
          wrapper.append(select);
          const trigger=document.createElement('button');
          trigger.type='button';
          trigger.className='personal-linked-relation-trigger';
          trigger.dataset.personalRelationTrigger=property.id;
          trigger.dataset.dbRow=select.dataset.dbRow;
          trigger.setAttribute('aria-label',`${property.name||'관계형'} 연결`);
          trigger.setAttribute('aria-haspopup','dialog');
          trigger.setAttribute('aria-expanded','false');
          trigger.textContent='대상 불러오는 중…';
          trigger.disabled=true;
          wrapper.append(trigger);
        }
        const key=JSON.stringify([property.id,property.relation_database_block_id||'',data.source_revision]);
        if (!slot.relationCache) {
          slot.relationCache=new Map();
          slot.relationCacheExpires=Date.now()+15000;
        }
        if (!slot.relationCache.has(key))
          slot.relationCache.set(key,Promise.resolve(host.relationOptions(scope,data,property)));
        void slot.relationCache.get(key).then(result=>{
          if (!body.isConnected || !host.current(scope)) return;
          const options=result.items||[];
          const known=new Set(options.map(item=>item.id));
          for (const select of controls) {
            const index=Number(select.dataset.dbRow);
            const row=data.block.props?.rows?.[index];
            const raw=row?.[property.id];
            const selected=new Set(Array.isArray(raw)
              ? raw.map(item=>String(item?.page_id||item||''))
              : String(raw||'').split(',').map(item=>item.trim()).filter(Boolean));
            const trigger=select.parentElement.querySelector('[data-personal-relation-trigger]');
            const blank=new Option('연결 없음','');
            select.replaceChildren(blank);
            for (const item of options) {
              const option=new Option(item.label,item.id);
              option.selected=selected.has(item.id);
              select.append(option);
            }
            for (const id of selected) if (!known.has(id)) {
              const option=new Option('현재 연결된 항목',id);
              option.selected=true;
              select.append(option);
            }
            const labels=new Map(options.map(item=>[item.id,item.label]));
            trigger.replaceChildren();
            if (!selected.size) {
              const empty=document.createElement('span');
              empty.className='personal-linked-relation-empty';
              empty.textContent='비어 있음';
              trigger.append(empty);
            } else {
              for (const id of [...selected].slice(0,2)) {
                const pill=document.createElement('span');
                pill.className='personal-linked-relation-pill';
                pill.textContent=labels.get(id)||'현재 연결된 항목';
                trigger.append(pill);
              }
              if (selected.size>2) {
                const extra=document.createElement('span');
                extra.className='personal-linked-relation-extra';
                extra.textContent=`+${selected.size-2}`;
                trigger.append(extra);
              }
            }
            trigger.disabled=!data.editable;
            if (!data.editable) continue;
            const commit=async value=>{
              if (!host.current(scope) || slot.saving) return;
              slot.saving=true;
              slot.node.querySelectorAll('[data-personal-relation-trigger]')
                .forEach(button=>{ button.disabled=true; });
              try { await host.commitCell(scope,slot.data || data,
                {row:index,field:property.id,value}); }
              catch(error) { host.error(error); }
              finally { slot.saving=false; await refresh(true); }
            };
            trigger.onclick=()=>{
              if (!host.current(scope) || slot.saving) return;
              slot.closeRelationPicker?.(false);
              const popover=document.createElement('div');
              popover.className='personal-linked-relation-popover';
              popover.setAttribute('role','dialog');
              popover.setAttribute('aria-label',`${property.name||'관계형'} 선택`);
              const search=document.createElement('input');
              search.type='search';
              search.placeholder='페이지 연결 또는 생성';
              search.setAttribute('aria-label','관계형 대상 검색');
              const header=document.createElement('div');
              header.className='personal-linked-relation-header';
              const sourceLabel=document.createElement('span');
              sourceLabel.className='personal-linked-relation-source';
              sourceLabel.textContent=`${result.title||'대상 데이터베이스'} 내 검색`;
              sourceLabel.title=sourceLabel.textContent;
              header.append(search,sourceLabel);
              const section=document.createElement('div');
              section.className='personal-linked-relation-section';
              const count=document.createElement('span');section.append(count);
              const list=document.createElement('div');
              list.className='personal-linked-relation-options';
              popover.append(header,section,list);
              document.body.append(popover);
              const position=()=>{
                const rect=trigger.getBoundingClientRect();
                const width=Math.min(560,window.innerWidth-16);
                popover.style.width=`${width}px`;
                popover.style.left=`${Math.max(8,Math.min(rect.left,window.innerWidth-width-8))}px`;
                const height=Math.min(320,popover.offsetHeight||320);
                let top=rect.bottom+5;
                if (top+height>window.innerHeight-8 && rect.top>height+8) top=rect.top-height-5;
                popover.style.top=`${Math.max(8,Math.min(top,window.innerHeight-height-8))}px`;
              };
              let propertyDisplay=null;
              const outside=event=>{
                if (!popover.contains(event.target) && !trigger.contains(event.target)) close(false);
              };
              const close=restore=>{
                propertyDisplay?.close(false);
                document.removeEventListener('pointerdown',outside,true);
                window.removeEventListener('resize',position);
                window.removeEventListener('scroll',position,true);
                popover.remove();
                trigger.setAttribute('aria-expanded','false');
                slot.relationPickerOpen=false;
                if (slot.closeRelationPicker===close) slot.closeRelationPicker=null;
                if (restore && trigger.isConnected) trigger.focus({preventScroll:true});
              };
              slot.closeRelationPicker=close;
              slot.relationPickerOpen=true;
              trigger.setAttribute('aria-expanded','true');
              document.addEventListener('pointerdown',outside,true);
              window.addEventListener('resize',position);
              window.addEventListener('scroll',position,true);
              const candidates=[...options,...[...selected].filter(id=>!known.has(id))
                .map(id=>({id,label:'현재 연결된 항목'}))];
              const choose=id=>{
                const next=new Set(selected);
                if (next.has(id)) next.delete(id);
                else if (!select.multiple) { next.clear();next.add(id); }
                else if (next.size<100) next.add(id);
                else { host.error(Error('관계형 연결은 최대 100개입니다.'));return; }
                close(false);
                void commit(select.multiple?[...next]:[...next][0]||'');
              };
              const draw=()=>{
                list.replaceChildren();
                const query=search.value.trim().toLocaleLowerCase();
                count.textContent=`${selected.size}개 선택함`;
                const available=candidates.filter(item=>!selected.has(item.id)
                  &&item.label.toLocaleLowerCase().includes(query));
                if (!available.length && (query || !selected.size)) {
                  const empty=document.createElement('p');
                  empty.className='personal-linked-relation-no-results';
                  empty.textContent='검색 결과가 없습니다.';
                  list.append(empty);
                }
                for (const item of [...candidates.filter(item=>selected.has(item.id)),
                  ...available]) {
                  const chosen=selected.has(item.id);
                  const button=document.createElement(chosen?'div':'button');
                  if (!chosen) button.type='button';
                  button.className='personal-linked-relation-option';
                  button.dataset.personalRelationOption=item.id;
                  button.dataset.selected=String(chosen);
                  if (chosen) {
                    button.setAttribute('role','group');
                    button.setAttribute('aria-label',`${item.label} 연결됨`);
                  } else button.setAttribute('aria-pressed','false');
                  const icon=document.createElement('span');
                  icon.className='personal-linked-relation-page-icon';
                  icon.innerHTML=item.icon&&globalThis.NotionIcons
                    ?globalThis.NotionIcons.html(item.icon):host.icon('page');
                  const label=document.createElement('span');
                  label.textContent=item.label;
                  propertyDisplay?.appendPreview(label,item);
                  const action=document.createElement(chosen?'button':'span');
                  action.className='personal-linked-relation-option-action';
                  action.textContent=chosen?'−':'＋';
                  if (chosen) {
                    action.type='button';
                    action.dataset.personalRelationRemove=item.id;
                    action.setAttribute('aria-label',`${item.label} 연결 해제`);
                    action.onclick=()=>choose(item.id);
                  } else button.onclick=()=>choose(item.id);
                  button.append(icon,label,action);
                  list.append(button);
                }
                position();
              };
              let composing=false;
              search.addEventListener('compositionstart',()=>{composing=true;});
              search.addEventListener('compositionend',()=>{composing=false;draw();});
              search.addEventListener('input',()=>{if (!composing) draw();});
              search.addEventListener('keydown',event=>{
                if (event.key==='ArrowDown') {
                  event.preventDefault();list.querySelector('button')?.focus();
                }
              });
              popover.addEventListener('keydown',event=>{
                if (event.key==='Escape' && !event.isComposing) {
                  event.preventDefault();event.stopPropagation();close(true);
                }
              });
              propertyDisplay=globalThis.NotionRelationDisplay?.create({
                popover,section,properties:result.properties,
                storageKey:JSON.stringify(['personal',scope.userId,scope.workspaceId,
                  data.document_id,data.block?.id,property.id,
                  property.relation_database_block_id]),onChange:draw,
              })||null;
              draw();
              search.focus({preventScroll:true});
            };
          }
        }).catch(error=>{
          if (!body.isConnected || !host.current(scope)) return;
          for (const select of controls) {
            select.replaceChildren(new Option('대상에 접근할 수 없습니다.',''));
            select.disabled=true;
            const trigger=select.parentElement.querySelector('[data-personal-relation-trigger]');
            trigger.textContent='대상에 접근할 수 없습니다.';
            trigger.disabled=true;
          }
          host.relationError?.(error);
        });
      }
    }

    function render(slot, data) {
      slot.closeRules?.();
      slot.closeRelationPicker?.(false);
      slot.closeFileMenu?.(false,false);
      const active = activeView(slot);
      if (!active || !data.block || data.block.id !== slot.block.props?.linked_database?.database_id)
        throw Error('데이터 소스가 변경되었습니다.');
      const views = slot.block.props.views || [];
      const activeFilters = Array.isArray(active.config?.filters) ? active.config.filters : [];
      const activeSorts = Array.isArray(active.config?.sorts) ? active.config.sorts : [];
      const propertyName = id => data.block.props?.properties?.find(item => item.id === id)?.name || id;
      const chips = [
        ...activeFilters.map(rule => `<button type="button" data-personal-linked-rule-chip="filters">${escape(propertyName(rule.property))} · ${escape(filterOperators[rule.operator] || '포함')}${['empty','not_empty'].includes(rule.operator) ? '' : `: ${escape(rule.value ?? '')}`}</button>`),
        ...(globalThis.NotionLinkedDatabases.filterGroupRuleCount(active.config?.filter_group)
          ? [`<button type="button" data-personal-linked-rule-chip="advanced">고급 필터 · 조건 ${globalThis.NotionLinkedDatabases.filterGroupRuleCount(active.config.filter_group)}개</button>`] : []),
        ...activeSorts.map(rule => `<button type="button" data-personal-linked-rule-chip="sorts">${escape(propertyName(rule.property))} ${rule.direction === 'desc' ? '↓' : '↑'}</button>`),
      ];
      const canManageViews = host.canEdit(scope);
      const fullPage = Boolean(host.fullPage?.(scope,slot.id));
      const fullPageControl=!host.fullPage?'':fullPage
        ?'<button type="button" data-personal-linked-full-page data-personal-linked-close-full-page aria-label="인라인 연결 보기로 돌아가기" title="인라인으로 돌아가기">×</button>'
        :host.fullPageUrl
        ?`<a href="${escape(host.fullPageUrl(scope,slot.id))}" data-personal-linked-full-page data-personal-linked-open-full-page aria-label="연결 보기 전체 페이지로 열기" title="전체 페이지로 열기">↗</a>`
        :'<button type="button" data-personal-linked-full-page data-personal-linked-open-full-page aria-label="연결 보기 전체 페이지로 열기" title="전체 페이지로 열기">↗</button>';
      const addView = canManageViews && views.length < 64
        ? `<button type="button" data-personal-linked-add-view aria-label="연결 보기 추가" title="보기 추가">＋</button>` : '';
      const viewTabs = views.length > 1
        ? `<div class="database-toolbar"><div class="database-view-tabs" role="tablist" aria-label="연결된 데이터베이스 보기">${views.map(view => `<button type="button" role="tab" data-personal-linked-view="${escape(view.id)}" aria-selected="${view.id === active.id}" class="${view.id === active.id ? 'active' : ''}">${escape(view.name)}</button>`).join('')}${addView}</div></div>` : '';
      slot.node.classList.add('personal-linked-database');
      slot.node.classList.toggle('linked-hide-source-title',active.config?.show_source_title === false);
      const activeSearch = slot.node.querySelector('[data-personal-linked-search-input]') === document.activeElement;
      const searchStart = activeSearch ? document.activeElement.selectionStart : null;
      const searchEnd = activeSearch ? document.activeElement.selectionEnd : null;
      const focusedFile = slot.node.querySelector('[data-db-file-link-form] input:focus');
      const fileFocus = focusedFile ? {
        key:focusedFile.closest('[data-personal-file-key]')?.dataset.personalFileKey,
        name:focusedFile.hasAttribute('data-db-file-link-name'),
        start:focusedFile.selectionStart,end:focusedFile.selectionEnd,
      } : slot.fileFocus;
      const viewMenuMarkup = canManageViews
        ? globalThis.NotionLinkedDatabases?.viewMenuHtml(active, data.block.props?.properties || [], host.icon,
          '현재 연결 보기 설정', data.block.props?.rows || []) || '' : '';
      slot.node.innerHTML = `<div class="linked-database-toolbar"><div class="personal-linked-source-cluster"><button type="button" class="linked-source" data-personal-linked-source title="원본 데이터베이스 열기" aria-label="원본 데이터베이스 열기">↗ <span class="linked-source-name">${escape(data.database.title)}</span></button>${views.length === 1 ? addView : ''}</div><div class="personal-linked-actions"><button type="button" data-personal-linked-filters aria-label="연결된 데이터베이스 필터" aria-haspopup="dialog" aria-expanded="false" title="필터" ${canManageViews ? '' : 'disabled'}>${host.icon('filter')}</button><button type="button" data-personal-linked-sorts aria-label="연결된 데이터베이스 정렬" aria-haspopup="dialog" aria-expanded="false" title="정렬" ${canManageViews ? '' : 'disabled'}>${host.icon('sort')}</button><button type="button" data-personal-linked-search aria-label="연결된 데이터베이스 검색" aria-expanded="${Boolean(slot.searchOpen)}" title="검색">${host.icon('search')}</button><button type="button" data-personal-linked-refresh aria-label="연결된 데이터베이스 새로고침" title="새로고침">↻</button>${fullPageControl}${canManageViews ? `<button type="button" data-personal-linked-view-settings aria-label="현재 연결 보기 설정" aria-haspopup="dialog" aria-expanded="false" title="보기 설정">${host.icon('settings')}</button>` : ''}${data.editable ? `<button type="button" class="personal-linked-create" data-personal-linked-create ${slot.pendingCreate?.busy ? 'disabled' : ''}>새로 만들기</button>` : ''}</div></div>${viewMenuMarkup}<div class="personal-linked-search" ${slot.searchOpen ? '' : 'hidden'}><input type="search" data-personal-linked-search-input aria-label="연결된 데이터베이스 항목 검색" placeholder="검색" maxlength="200" value="${escape(slot.query || '')}"></div>${viewTabs}${chips.length ? `<div class="personal-linked-rule-chips" aria-label="현재 보기 조건">${chips.join('')}</div>` : ''}<div class="personal-linked-rules" data-personal-linked-rules role="dialog" aria-label="보기 조건" hidden></div><div class="personal-linked-preview"></div>`;
      renderPreview(slot, data);
      const body = slot.node.querySelector('.personal-linked-preview');
      const headerSvg = {날짜:'calendar',링크:'link',이메일:'mail',관계형:'link',파일:'attachment'};
      const headerGlyph = {텍스트:'Aa',체크박스:'✓',선택:'▤',상태:'◉','다중 선택':'▤',사람:'♙',전화번호:'☎'};
      body.querySelectorAll('.table-header > span > small,.table-header > button > small').forEach(label => {
        const type = label.title;
        if (headerSvg[type]) label.innerHTML = host.icon(headerSvg[type]);
        else if (headerGlyph[type]) label.textContent = headerGlyph[type];
        label.setAttribute('aria-hidden', 'true');
        label.parentElement.prepend(label);
      });
      body.querySelectorAll('.database-head,.database-toolbar,.database-add-property,.board-view-controls,.calendar-add').forEach(node => node.remove());
      body.querySelectorAll('button,input,select,textarea').forEach(control => { control.disabled = true; });
      const rows = Array.isArray(data.block.props?.rows) ? data.block.props.rows : [];
      body.querySelectorAll('button[data-table-row]').forEach(button => {
        const item = rows[Number(button.dataset.tableRow)];
        if (!item?.page_id) return;
        button.disabled = false;
        button.addEventListener('click', () => {
          if (host.current(scope)) host.openItem(item.page_id);
        });
      });
      body.querySelectorAll('button[data-calendar-page]').forEach(button => {
        if (!button.dataset.calendarPage) return;
        button.disabled = false;
        button.addEventListener('click', () => {
          if (host.current(scope)) host.openItem(button.dataset.calendarPage);
        });
      });
      if (data.editable) {
        const properties = Array.isArray(data.block.props?.properties) ? data.block.props.properties : [];
        body.querySelectorAll('input[data-db-row][data-db-field],select[data-db-row][data-db-field],textarea[data-db-row][data-db-field]').forEach(control => {
          const property = properties.find(item => item.id === control.dataset.dbField);
          if (!editableTypes.has(property?.type)) return;
          control.disabled = false;
          control.addEventListener('change', async () => {
            if (!host.current(scope) || slot.saving) return;
            const value = control.type === 'checkbox' ? control.checked
              : control.multiple ? [...control.selectedOptions].map(option => option.value).filter(Boolean).join(',')
                : control.value;
            slot.saving = true;
            control.disabled = true;
            try {
              await host.commitCell(scope, data, {row:Number(control.dataset.dbRow),field:control.dataset.dbField,value});
            } catch (error) { host.error(error); }
            finally { slot.saving = false; await refresh(true); }
          });
        });
        body.querySelectorAll('button[data-db-row][data-db-date-picker]').forEach(button => {
          const field = button.dataset.dbDatePicker;
          if (properties.find(property => property.id === field)?.type !== 'date') return;
          const initialIndex = Number(button.dataset.dbRow);
          const initial = rows[initialIndex];
          if (!initial) return;
          const rowId = String(initial._canonical_row_id || initial.page_id || initial.id || '');
          button.disabled = false;
          button.addEventListener('click', () => {
            if (!host.current(scope) || slot.saving) return;
            const latestRows = slot.data?.block?.props?.rows || [];
            const index = rowId ? latestRows.findIndex(item =>
              String(item._canonical_row_id || item.page_id || item.id || '') === rowId) : initialIndex;
            const item = latestRows[index];
            if (!item) return;
            const raw = item[field];
            const value = raw && typeof raw === 'object' ? raw
              : {start:raw || '',end:item[`${field}_end`] || ''};
            host.openDate(button, value, async next => {
              if (!host.current(scope) || slot.saving) throw Error('날짜 셀이 변경되었습니다.');
              const currentRows = slot.data?.block?.props?.rows || [];
              const row = rowId ? currentRows.findIndex(candidate =>
                String(candidate._canonical_row_id || candidate.page_id || candidate.id || '') === rowId) : initialIndex;
              if (!currentRows[row]) throw Error('원본 항목이 변경되었습니다.');
              slot.saving = true;
              try {
                await host.commitCell(scope, slot.data, {row,field,value:next});
              } finally { slot.saving = false; }
              await refresh(true);
              const candidates = [...slot.node.querySelectorAll('button[data-db-date-picker]')];
              const reopenAnchor = candidates.find(candidate => candidate.dataset.dbDatePicker === field
                && (rowId ? candidate.dataset.dbRowId === rowId
                  : Number(candidate.dataset.dbRow) === initialIndex));
              return {reopenAnchor};
            });
          });
        });
        bindFileCells(slot,data,body);
      }
      host.bindFileActions?.(body);
      bindRelationCells(slot,data,body);
      slot.node.querySelector('[data-personal-linked-source]').onclick = () => host.open(data.document_id);
      slot.node.querySelector('[data-personal-linked-refresh]').onclick = () => refresh(true);
      slot.node.querySelector('[data-personal-linked-full-page]')?.addEventListener('click',event=>{
        if(event.currentTarget.tagName==='A'){
          if(event.metaKey||event.ctrlKey||event.shiftKey||event.altKey)return;
          event.preventDefault();
        }
        if(host.current(scope)&&slot.node.isConnected)host.toggleFullPage?.(scope,slot.id);
      });
      slot.node.querySelector('[data-personal-linked-add-view]')?.addEventListener('click', () => openViewSettings(slot, data, true));
      bindViewMenu(slot, data);
      for (const kind of ['filters','sorts'])
        slot.node.querySelector(`[data-personal-linked-${kind}]`).onclick = () =>
          kind === 'filters' && active.config?.filter_group ? openAdvancedRules(slot,data) : openRules(slot,data,kind);
      slot.node.querySelectorAll('[data-personal-linked-rule-chip]').forEach(button => {
        button.onclick = () => button.dataset.personalLinkedRuleChip === 'advanced'
          ? openAdvancedRules(slot,data) : openRules(slot, data, button.dataset.personalLinkedRuleChip);
      });
      const createButton = slot.node.querySelector('[data-personal-linked-create]');
      if (createButton) createButton.onclick = async () => {
        if (!host.current(scope) || slot.pendingCreate?.busy || !data.editable) return;
        if (slot.pendingCreate?.completed) slot.pendingCreate = null;
        slot.pendingCreate ||= {
          base_revision: data.source_revision,
          operation_id: `personal-linked-item-${crypto.randomUUID()}`,
        };
        const pending = slot.pendingCreate;
        pending.busy = true;
        createButton.disabled = true;
        try {
          const result = await host.createItem(scope, data, pending);
          pending.completed = true;
          if (!host.current(scope)) return;
          await refresh(true);
          if (host.current(scope)) host.openItem(result.item_page_id);
        } catch (error) {
          if (error?.status) pending.completed = true;
          host.error(error);
          await refresh(true);
        } finally {
          pending.busy = false;
          slot.node.querySelector('[data-personal-linked-create]')?.removeAttribute('disabled');
        }
      };
      const search = slot.node.querySelector('[data-personal-linked-search-input]');
      const updateSearch = () => {
        slot.query = search.value;
        if (!slot.saving) render(slot, data);
      };
      search.addEventListener('input', () => { if (!slot.composing) updateSearch(); });
      search.addEventListener('compositionstart', () => { slot.composing = true; });
      search.addEventListener('compositionend', () => { slot.composing = false; updateSearch(); });
      search.addEventListener('keydown', event => {
        if (event.key === 'Escape' && !event.isComposing && !slot.composing) {
          event.preventDefault(); event.stopPropagation();
          slot.query = ''; slot.searchOpen = false;
          render(slot, data);
          slot.node.querySelector('[data-personal-linked-search]').focus();
        }
      });
      slot.node.querySelector('[data-personal-linked-search]').onclick = () => {
        slot.searchOpen = !slot.searchOpen;
        if (!slot.searchOpen) slot.query = '';
        render(slot, data);
        (slot.searchOpen ? slot.node.querySelector('[data-personal-linked-search-input]')
          : slot.node.querySelector('[data-personal-linked-search]')).focus();
      };
      if (activeSearch && slot.searchOpen) {
        search.focus({preventScroll:true});
        if (searchStart !== null) search.setSelectionRange(searchStart, searchEnd);
      }
      if (fileFocus?.key) {
        const form=[...body.querySelectorAll('[data-personal-file-key]')]
          .find(item=>item.dataset.personalFileKey===fileFocus.key);
        const input=form?.querySelector(fileFocus.name ? '[data-db-file-link-name]' : '[data-db-file-link-url]');
        if (input && !form.hidden) {
          input.focus({preventScroll:true});
          if (fileFocus.start !== null) input.setSelectionRange(fileFocus.start,fileFocus.end);
        } else slot.fileFocus=null;
      }
      slot.node.querySelectorAll('[data-personal-linked-view]').forEach(button => {
        button.onclick = () => {
          if (!host.current(scope)) return;
          const view = slot.block.props.views.find(item => item.id === button.dataset.personalLinkedView);
          if (!view || view.id === activeView(slot)?.id) return;
          if (host.canEdit(scope)) host.selectView(scope, slot.block, view);
          else { slot.localViewId = view.id; render(slot, slot.data); }
        };
      });
      slot.data = data;
      host.updated?.(scope,slot.id,data);
    }

    async function refresh(force = false) {
      if (!scope || !host.current(scope)) { clear(); return; }
      if (flight) { await flight; if (force && scope && host.current(scope)) return refresh(true); return; }
      const context = scope, version = generation;
      abort = new AbortController();
      const signal = abort.signal;
      flight = (async () => {
        let cursor = 0;
        async function worker() {
          while (cursor < slots.length && current(version, context) && !signal.aborted) {
            const slot = slots[cursor++];
            if (!slot.node.isConnected) continue;
            try {
              const data = await host.load(context, slot.block, signal);
              if (!current(version, context) || signal.aborted) return;
              const sameData=Boolean(slot.data && JSON.stringify(slot.data)===JSON.stringify(data));
              const focusedControl=Boolean(slot.node.querySelector('.personal-linked-preview')
                ?.contains(document.activeElement) && document.activeElement?.matches('input,select,textarea'));
              if (slot.saving || (sameData && (slot.relationPickerOpen
                  || slot.closeFileMenu || (!force && focusedControl)))) continue;
              const staleRelations=Boolean(slot.relationCacheExpires && Date.now()>=slot.relationCacheExpires);
              if (!force && !staleRelations && sameData) continue;
              if (force || staleRelations) {
                slot.relationCache=null;
                slot.relationCacheExpires=0;
              }
              render(slot, data);
            } catch (error) {
              if (!current(version, context) || signal.aborted) return;
              slot.closeRules?.();
              slot.closeRelationPicker?.(false);
              slot.closeFileMenu?.(false);
              slot.data = null;
              slot.relationCache = null;
              slot.relationCacheExpires = 0;
              slot.fileDrafts?.clear();
              slot.node.innerHTML = `<p class="linked-database-status" role="status">${[401, 403, 404].includes(error?.status) ? '원본에 접근할 수 없습니다.' : '데이터 소스를 불러오지 못했습니다.'}</p><button type="button" data-personal-linked-retry>다시 불러오기</button>`;
              slot.node.querySelector('[data-personal-linked-retry]').onclick = () => refresh(true);
            }
          }
        }
        await Promise.all([worker(), worker()]);
      })();
      const pending = flight;
      try { await pending; }
      finally { if (flight === pending) { flight = null; abort = null; } }
    }

    function mount(context, nextSlots) {
      const retained = scope?.pageId === context?.pageId && scope?.userId === context?.userId
        ? new Map(slots.map(slot => [slot.id, slot])) : new Map();
      const retainedMenu = retained.size ? menuState : null;
      clear();
      scope = context;
      menuState = retainedMenu;
      slots = nextSlots.map(slot => {
        const before = retained.get(slot.id);
        if (before?.block?.props?.linked_database?.document_id
            === slot.block?.props?.linked_database?.document_id
            && before?.block?.props?.linked_database?.database_id
              === slot.block?.props?.linked_database?.database_id) {
          slot.query = before.query || '';
          slot.searchOpen = Boolean(before.searchOpen);
          slot.pendingCreate = before.pendingCreate || null;
          slot.localViewId = before.localViewId || null;
          slot.fileDrafts = before.fileDrafts || null;
          slot.fileFocus = before.fileFocus || null;
          slot.relationCache = before.relationCache || null;
          slot.relationCacheExpires = before.relationCacheExpires || 0;
        }
        return slot;
      });
      void refresh();
    }

    return {mount, refresh, clear};
  }

  return {create};
});

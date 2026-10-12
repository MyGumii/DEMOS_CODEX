(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  root.NotionGridSelection = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";
  let active = null;
  let pendingRestore = null;
  const gutterBindings = new WeakMap();
  const gutterSelectedGrids = new Set();
  function clearGutterRows() {
    for (const grid of [...gutterSelectedGrids]) gutterBindings.get(grid)?.setGutterRows([]);
  }
  function gutterRowSnapshot() {
    return [...gutterSelectedGrids].filter(grid => grid.isConnected).map(grid => ({
      grid, rows: gutterBindings.get(grid)?.selectedRows() || [],
    }));
  }
  function syncGutterRows(selections = []) {
    const requested = new Map(selections.filter(entry => gutterBindings.has(entry.grid))
      .map(entry => [entry.grid, entry.rows]));
    for (const grid of [...gutterSelectedGrids]) {
      if (!requested.has(grid) || !grid.isConnected) gutterBindings.get(grid)?.setGutterRows([]);
    }
    for (const [grid, keys] of requested) if (grid.isConnected) gutterBindings.get(grid).setGutterRows(keys);
  }
  function edgeScrollDelta(position, start, end, backward, forward) {
    const edge = Math.min(36, Math.max(0, (end-start)/4));
    if (!edge) return 0;
    if (backward && position < start+edge) return -Math.min(18, Math.max(1, Math.ceil((start+edge-position)/2)));
    if (forward && position > end-edge) return Math.min(18, Math.max(1, Math.ceil((position-end+edge)/2)));
    return 0;
  }
  function rectangle(rows, columns, anchor, focus) {
    const a = [rows.indexOf(anchor.row), columns.indexOf(anchor.column)];
    const b = [rows.indexOf(focus.row), columns.indexOf(focus.column)];
    if ([...a, ...b].some(i => i < 0)) return [];
    const result = [];
    for (let r = Math.min(a[0], b[0]); r <= Math.max(a[0], b[0]); r++) {
      for (let c = Math.min(a[1], b[1]); c <= Math.max(a[1], b[1]); c++) result.push({row: rows[r], column: columns[c]});
    }
    return result;
  }
  function parseTsv(text) {
    const rows = [[]]; let value = "", quoted = false;
    for (let i = 0; i < text.length; i++) {
      const ch = text[i];
      if (ch === '"' && (quoted || !value)) {
        if (quoted && text[i + 1] === '"') { value += '"'; i++; } else quoted = !quoted;
      } else if (!quoted && (ch === "\t" || ch === "\n" || ch === "\r")) {
        rows.at(-1).push(value); value = "";
        if (ch !== "\t") { if (ch === "\r" && text[i + 1] === "\n") i++; rows.push([]); }
      } else value += ch;
    }
    if (quoted) throw new Error("닫히지 않은 따옴표가 있습니다.");
    if (value || rows.at(-1).length || rows.length === 1) rows.at(-1).push(value); else rows.pop();
    if (rows.some(row => row.length !== rows[0].length)) throw new Error("붙여넣을 각 행의 열 수가 다릅니다.");
    return rows;
  }
  function toTsv(rows) {
    return rows.map(row => row.map(value => {
      const text = String(value ?? "");
      return /[\t\n\r"]/.test(text) ? '"' + text.replace(/"/g, '""') + '"' : text;
    }).join("\t")).join("\n");
  }
  function typedValue(type, raw, options = []) {
    const text = String(raw ?? "").trim();
    if (["formula", "rollup", "created_time", "last_edited_time", "created_by", "last_edited_by", "unique_id", "files", "relation", "button"].includes(type)) throw new Error("이 속성은 셀 편집 도구에서 변경하세요.");
    if (type === "number") { if (!text) return ""; if (!Number.isFinite(Number(text))) throw new Error("숫자를 입력하세요."); return Number(text); }
    if (type === "checkbox") { if (/^(true|1|yes|체크됨)$/i.test(text)) return true; if (/^(false|0|no|체크 안 됨)?$/i.test(text)) return false; throw new Error("체크박스에는 true 또는 false를 입력하세요."); }
    if (type === "date" && text && (!/^\d{4}-\d{2}-\d{2}$/.test(text) || !Number.isFinite(Date.parse(text)) || new Date(text).toISOString().slice(0, 10) !== text)) throw new Error("날짜는 YYYY-MM-DD 형식으로 입력하세요.");
    if (type === "email" && text && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(text)) throw new Error("올바른 이메일을 입력하세요.");
    if (type === "url" && text && !/^https?:\/\/[^\s]+$/i.test(text)) throw new Error("http 또는 https 주소를 입력하세요.");
    if (type === "person" && text && !options.includes(text)) throw new Error("목록에 있는 사용자를 선택하세요.");
    return type === "text" || type === "title" ? String(raw ?? "") : text;
  }
  function bind(grid, host) {
    if (!grid || grid.dataset.gridBound) return;
    grid.dataset.gridBound = "true"; grid.tabIndex = 0; grid.setAttribute("role", "grid");
    grid.setAttribute("aria-label", "표 셀 · Enter 편집, Shift 방향키 범위 선택");
    const cells = [...grid.querySelectorAll("[data-grid-row][data-grid-column]")];
    const rows = [...new Set(cells.map(cell => cell.dataset.gridRow))];
    const columns = [...new Set(cells.map(cell => cell.dataset.gridColumn))];
    const key = cell => ({row: cell.dataset.gridRow, column: cell.dataset.gridColumn});
    const cellAt = value => cells.find(cell => cell.dataset.gridRow === value?.row && cell.dataset.gridColumn === value?.column);
    let anchor = null, focus = null, selected = [], editing = false, pointer = null, suppressClick = false, clickEdits = false;
    let gutterRows = new Set(), gutterToolbar = null, duplicateSelectedRows = null;
    const dataRows = [...grid.querySelectorAll('.table-row:not(.table-header)')];
    const dataRowKey = row => row.querySelector('[data-grid-row]')?.dataset.gridRow || '';
    let scrollFrame = 0;
    cells.forEach(cell => { cell.setAttribute("role", "gridcell"); cell.setAttribute("aria-selected", "false"); });
    grid.addEventListener("focusin",event=>{
      const cell=event.target.closest("[data-grid-row][data-grid-column]");
      if(cell && event.target.matches("input,select,textarea") && !event.target.disabled && !pointer) {
        if(!focus || cell!==cellAt(focus)) select(key(cell));
        editing=true;paint();
      }
    });
    function paint() {
      const included = new Set(selected.map(cellAt));
      cells.forEach(cell => { const on = included.has(cell); cell.classList.toggle("grid-cell-selected", on); cell.classList.toggle("grid-cell-active", !editing && cell === cellAt(focus)); cell.setAttribute("aria-selected", String(on)); });
      dataRows.forEach(row => {
        const on = gutterRows.has(dataRowKey(row));
        row.classList.toggle('database-row-selected', on);
        const checkbox = row.querySelector('.database-row-select-control input');
        if (checkbox) checkbox.checked = on;
      });
      grid.classList.toggle('database-rows-selected', gutterRows.size > 0);
      if (gutterToolbar) {
        gutterToolbar.querySelector('[data-selected-count]').textContent = `${gutterRows.size}개 선택됨`;
        const links = host.rowLinks?.([...gutterRows]) || [];
        const more = gutterToolbar.querySelector('[data-selected-row-more]');
        const canCopy = gutterRows.size > 0 && links.length === gutterRows.size;
        const canTrash = gutterRows.size > 0 && Boolean(host.canTrashRows?.([...gutterRows]));
        const canEditIcons = gutterRows.size > 0 && Boolean(host.canEditRowsIcon?.([...gutterRows]));
        const canEditProperties = gutterRows.size > 0 && Boolean(host.editableRowProperties?.([...gutterRows])?.length);
        const canDuplicate = gutterRows.size > 0 && Boolean(host.canDuplicateRows?.([...gutterRows]));
        const canMove = gutterRows.size > 0 && Boolean(host.canMoveRows?.([...gutterRows]));
        gutterToolbar.querySelector('[data-trash-selected-rows]').hidden = !canTrash;
        gutterToolbar.querySelector('[data-copy-selected-links]').hidden = !canCopy;
        gutterToolbar.querySelector('[data-menu-trash-selected-rows]').hidden = !canTrash;
        gutterToolbar.querySelector('[data-edit-selected-icons]').hidden = !canEditIcons;
        gutterToolbar.querySelector('[data-edit-selected-properties]').hidden = !canEditProperties;
        gutterToolbar.querySelector('[data-duplicate-selected-rows]').hidden = !canDuplicate;
        gutterToolbar.querySelector('[data-move-selected-rows]').hidden = !canMove;
        gutterToolbar.querySelector('.database-row-selection-heading').hidden = !canEditIcons && !canEditProperties;
        if (more) more.hidden = !canCopy && !canTrash && !canEditIcons && !canEditProperties && !canDuplicate && !canMove;
        const all = grid.querySelector('[data-select-all-rows]');
        if (all) {
          all.checked = dataRows.length > 0 && gutterRows.size === dataRows.length;
          all.indeterminate = gutterRows.size > 0 && gutterRows.size < dataRows.length;
        }
      }
    }
    function removeGutterControls() {
      if (document.activeElement?.closest?.('.database-row-select-control')
          && grid.contains(document.activeElement)) grid.focus({preventScroll:true});
      if (gutterToolbar) {
        gutterToolbar.closeSelectionMenu?.();
        gutterToolbar.hidden = true;
      }
      grid.querySelectorAll('.database-row-select-control').forEach(control => { control.hidden = true; });
    }
    function ensureGutterControls() {
      if (gutterToolbar) {
        gutterToolbar.hidden = false;
        grid.querySelectorAll('.database-row-select-control').forEach(control => { control.hidden = false; });
        return;
      }
      if (!dataRows.length) return;
      gutterToolbar = document.createElement('div');
      gutterToolbar.className = 'database-row-selection-toolbar';
      gutterToolbar.innerHTML = '<button type="button" data-clear-selected-rows><span data-selected-count></span></button><button type="button" data-trash-selected-rows aria-label="선택한 페이지를 휴지통으로 이동" hidden><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 6h18M8 6V4h8v2m3 0-1 14H6L5 6m5 4v6m4-6v6"/></svg></button><button type="button" data-selected-row-more aria-label="선택한 페이지 작업" aria-haspopup="menu" aria-expanded="false" hidden>···</button><div class="database-row-selection-menu" role="menu" hidden><span class="database-row-selection-heading" hidden>페이지</span><button type="button" role="menuitem" data-edit-selected-icons hidden>아이콘 편집</button><button type="button" role="menuitem" data-edit-selected-properties hidden>속성 편집 <span aria-hidden="true">›</span></button><div class="database-row-property-menu" role="menu" aria-label="편집할 속성" hidden></div><button type="button" role="menuitem" data-copy-selected-links>모든 링크 복사</button><button type="button" role="menuitem" data-duplicate-selected-rows hidden>복제 <small>Ctrl+D</small></button><button type="button" role="menuitem" data-move-selected-rows hidden>옮기기 <small>›</small></button><button type="button" role="menuitem" data-menu-trash-selected-rows hidden>휴지통으로 이동</button></div>';
      grid.parentElement.insertBefore(gutterToolbar, grid);
      const more = gutterToolbar.querySelector('[data-selected-row-more]');
      const menu = gutterToolbar.querySelector('.database-row-selection-menu');
      const propertyMenu = menu.querySelector('.database-row-property-menu');
      const outside = event => { if (!gutterToolbar.contains(event.target)) closeMenu(); };
      const closeMenu = () => {
        menu.hidden = true;
        propertyMenu.hidden = true;
        more.setAttribute('aria-expanded', 'false');
        document.removeEventListener('pointerdown', outside, true);
      };
      gutterToolbar.closeSelectionMenu = closeMenu;
      more.addEventListener('click', () => {
        if (!menu.hidden) { closeMenu(); return; }
        menu.hidden = false;
        more.setAttribute('aria-expanded', 'true');
        document.addEventListener('pointerdown', outside, true);
        menu.querySelector('[role="menuitem"]:not([hidden])')?.focus({preventScroll:true});
      });
      menu.querySelector('[data-edit-selected-icons]').addEventListener('click', () => {
        const keys = [...gutterRows];
        closeMenu();
        void Promise.resolve(host.openRowsIconPicker?.(keys, more))
          .catch(error => host.error?.(error?.message || '아이콘 선택창을 열지 못했습니다.'));
      });
      menu.querySelector('[data-edit-selected-properties]').addEventListener('click', () => {
        const properties = host.editableRowProperties?.([...gutterRows]) || [];
        if (!properties.length) { closeMenu(); return; }
        propertyMenu.replaceChildren();
        for (const property of properties) {
          const button = document.createElement('button');
          button.type = 'button';
          button.setAttribute('role', 'menuitem');
          button.textContent = property.name || property.id;
          button.addEventListener('click', () => {
            const keys = [...gutterRows];
            closeMenu();
            void Promise.resolve(host.openRowsPropertyEditor?.(keys, property.id, more))
              .catch(error => host.error?.(error?.message || '속성을 편집하지 못했습니다.'));
          });
          propertyMenu.appendChild(button);
        }
        propertyMenu.hidden = false;
        propertyMenu.querySelector('button')?.focus({preventScroll:true});
      });
      menu.querySelector('[data-copy-selected-links]').addEventListener('click', () => {
        const keys = [...gutterRows];
        closeMenu();
        void Promise.resolve(host.copyRowsLinks?.(keys)).catch(error => host.error?.(error?.message || '링크를 복사하지 못했습니다.'));
      });
      let duplicatePending = false;
      duplicateSelectedRows = () => {
        if (duplicatePending || !gutterRows.size || !host.canDuplicateRows?.([...gutterRows])) return;
        const keys = [...gutterRows];
        closeMenu(); duplicatePending = true;
        gutterToolbar.setAttribute('aria-busy', 'true'); more.disabled = true;
        void Promise.resolve().then(() => host.duplicateRows?.(keys)).catch(error =>
          host.error?.(error?.message || '선택한 페이지를 복제하지 못했습니다.'))
          .finally(() => {
            duplicatePending = false;
            gutterToolbar.removeAttribute('aria-busy'); more.disabled = false;
          });
      };
      menu.querySelector('[data-duplicate-selected-rows]').addEventListener('click', duplicateSelectedRows);
      menu.querySelector('[data-move-selected-rows]').addEventListener('click', () => {
        const keys=[...gutterRows];
        if(!host.canMoveRows?.(keys))return;
        closeMenu();
        void Promise.resolve(host.openRowsMovePicker?.(keys,more))
          .catch(error=>host.error?.(error?.message||'선택한 페이지를 옮기지 못했습니다.'));
      });
      let trashPending = false;
      const trashSelected = () => {
        if (trashPending || !gutterRows.size) return;
        const keys = [...gutterRows];
        closeMenu();
        trashPending = true;
        gutterToolbar.setAttribute('aria-busy', 'true');
        gutterToolbar.querySelector('[data-trash-selected-rows]').disabled = true;
        more.disabled = true;
        void Promise.resolve().then(() => host.trashRows?.(keys)).catch(error =>
          host.error?.(error?.message || '선택한 페이지를 휴지통으로 이동하지 못했습니다.'))
          .finally(() => {
            trashPending = false;
            gutterToolbar.removeAttribute('aria-busy');
            gutterToolbar.querySelector('[data-trash-selected-rows]').disabled = false;
            more.disabled = false;
          });
      };
      gutterToolbar.querySelector('[data-trash-selected-rows]').addEventListener('click', trashSelected);
      menu.querySelector('[data-menu-trash-selected-rows]').addEventListener('click', trashSelected);
      menu.addEventListener('keydown', event => {
        if (event.key === 'Escape') {
          event.preventDefault(); event.stopPropagation();
          if (!propertyMenu.hidden) { propertyMenu.hidden = true; menu.querySelector('[data-edit-selected-properties]').focus({preventScroll:true}); }
          else { closeMenu(); more.focus({preventScroll:true}); }
        }
      });
      const headerLabel = document.createElement('label');
      headerLabel.className = 'database-row-select-control database-select-header-control';
      headerLabel.innerHTML = '<input type="checkbox" data-select-all-rows aria-label="표의 모든 행 선택">';
      grid.querySelector('.table-header')?.appendChild(headerLabel);
      headerLabel.querySelector('input').addEventListener('change', event => {
        host.clearBlocks();
        setGutterRows(event.target.checked ? dataRows.map(dataRowKey) : []);
      });
      gutterToolbar.querySelector('[data-clear-selected-rows]').addEventListener('click', () => setGutterRows([]));
      dataRows.forEach((row, index) => {
        const label = document.createElement('label');
        label.className = 'database-row-select-control';
        label.innerHTML = `<input type="checkbox" aria-label="${index + 1}행 선택">`;
        label.querySelector('input').addEventListener('change', event => {
          host.clearBlocks();
          const next = new Set(gutterRows);
          if (event.target.checked) next.add(dataRowKey(row));
          else next.delete(dataRowKey(row));
          setGutterRows([...next]);
        });
        row.appendChild(label);
      });
    }
    function setGutterRows(keys) {
      const next = new Set(keys.filter(key => rows.includes(key)));
      if (next.size === gutterRows.size && [...next].every(key => gutterRows.has(key))) return;
      if (next.size && active) active.clear();
      gutterRows = next;
      if (next.size) gutterSelectedGrids.add(grid);
      else gutterSelectedGrids.delete(grid);
      if (gutterRows.size) ensureGutterControls();
      else removeGutterControls();
      paint();
    }
    gutterBindings.set(grid, {setGutterRows, selectedRows: () => [...gutterRows]});
    function clear() { selected = []; anchor = focus = null; editing = false; gutterRows.clear(); gutterSelectedGrids.delete(grid); removeGutterControls(); paint(); if (active?.grid === grid) active = null; }
    function select(value, extend = false) {
      if (active?.grid !== grid) active?.clear();
      clearGutterRows();
      active = {grid, clear}; host.clearBlocks();
      if (!extend || !anchor) anchor = value;
      focus = value; editing = false; selected = rectangle(rows, columns, anchor, focus); paint();
    }
    function beginEdit() {
      if (!host.canEdit() || !focus) return;
      const cell = cellAt(focus), input = cell.matches("input") ? cell : cell.querySelector("input:not(:disabled),select:not(:disabled),textarea:not(:disabled)");
      if (!input || input.disabled) return;
      editing = true; selected = [focus]; paint(); input.focus();
      if (!["checkbox", "date"].includes(input.type)) input.select?.();
    }
    function move(dr, dc, extend) {
      const r = Math.max(0, Math.min(rows.length - 1, rows.indexOf(focus.row) + dr));
      const c = Math.max(0, Math.min(columns.length - 1, columns.indexOf(focus.column) + dc));
      select({row: rows[r], column: columns[c]}, extend); grid.focus({preventScroll: true}); cellAt(focus)?.scrollIntoView({block:"nearest", inline:"nearest"});
    }
    function finishEdit(value) {
      editing = false;
      pendingRestore = {id:host.id, value};
      grid.focus(); // Commits the native input; the host may rebuild this grid.
      if (grid.isConnected) {select(value);grid.focus({preventScroll:true});}
    }
    function scrollParent(axis) {
      for (let node=grid; node && node!==document.body; node=node.parentElement) {
        const style=getComputedStyle(node), overflow=axis==='x'?style.overflowX:style.overflowY;
        if (/auto|scroll/.test(overflow) && (axis==='x'?node.scrollWidth>node.clientWidth:node.scrollHeight>node.clientHeight)) return node;
      }
      return axis==='y' ? document.scrollingElement : null;
    }
    function extendAtPointer() {
      if (!pointer) return;
      const x=Math.max(1,Math.min(innerWidth-2,pointer.lastX)), y=Math.max(1,Math.min(innerHeight-2,pointer.lastY));
      let cell=document.elementFromPoint(x,y)?.closest('[data-grid-row][data-grid-column]');
      if (!cell || !grid.contains(cell)) {
        let distance=Infinity;cell=null;
        for (const candidate of cells) {
          const r=candidate.getBoundingClientRect();
          if (!r.width || !r.height || r.bottom<0 || r.top>innerHeight || r.right<0 || r.left>innerWidth) continue;
          const d=Math.max(r.left-x,0,x-r.right)**2 + Math.max(r.top-y,0,y-r.bottom)**2;
          if (d<distance) {distance=d;cell=candidate;}
        }
      }
      if (cell && (focus?.row!==cell.dataset.gridRow || focus?.column!==cell.dataset.gridColumn)) select(key(cell),true);
    }
    function scrollSelection() {
      scrollFrame=0;
      if (!pointer || !pointer.moving || !grid.isConnected) return;
      const bounds=grid.getBoundingClientRect();let moved=false;
      for (const axis of ['x','y']) {
        const scroller=scrollParent(axis);if(!scroller)continue;
        const root=scroller===document.scrollingElement;
        const r=root?{left:0,right:innerWidth,top:0,bottom:innerHeight}:scroller.getBoundingClientRect();
        const start=axis==='x'?Math.max(0,r.left):Math.max(48,r.top), end=axis==='x'?Math.min(innerWidth,r.right):Math.min(innerHeight,r.bottom);
        const delta=edgeScrollDelta(axis==='x'?pointer.lastX:pointer.lastY,start,end,axis==='x'?bounds.left<start:bounds.top<start,axis==='x'?bounds.right>end:bounds.bottom>end);
        const property=axis==='x'?'scrollLeft':'scrollTop', old=scroller[property];
        scroller[property]+=delta;moved ||= scroller[property]!==old;
      }
      extendAtPointer();
      if(moved)scrollFrame=requestAnimationFrame(scrollSelection);
    }
    grid.addEventListener("pointerdown", event => {
      if (event.target.closest('.database-row-select-control')) return;
      const cell = event.target.closest("[data-grid-row][data-grid-column]");
      if (!cell || event.button !== 0 || event.target.closest("button,a") || (editing && cell === cellAt(focus) && !event.shiftKey)) return;
      event.preventDefault(); event.stopPropagation();
      suppressClick=false;
      select(key(cell), event.shiftKey); clickEdits = !event.shiftKey; grid.focus({preventScroll:true});
      pointer = {id:event.pointerId, x:event.clientX, y:event.clientY,lastX:event.clientX,lastY:event.clientY,moving:false}; grid.setPointerCapture(event.pointerId);
    });
    grid.addEventListener("pointermove", event => {
      if (!pointer || pointer.id !== event.pointerId || (!pointer.moving && Math.hypot(event.clientX-pointer.x,event.clientY-pointer.y)<4)) return;
      pointer.lastX=event.clientX;pointer.lastY=event.clientY;pointer.moving=true;
      extendAtPointer();
      if(!scrollFrame)scrollFrame=requestAnimationFrame(scrollSelection);
      event.preventDefault(); suppressClick = true;
    });
    const release = () => { cancelAnimationFrame(scrollFrame);scrollFrame=0;if (pointer) {const id=pointer.id;pointer=null;try {grid.releasePointerCapture(id);} catch (_) {}} };
    grid.addEventListener("pointerup",release); grid.addEventListener("pointercancel",release);
    grid.addEventListener("lostpointercapture",release);
    grid.addEventListener("click",event => {
      // A row opener or file link remains clickable inside a selected cell.
      if (!suppressClick && event.target.closest("button,a")) { clickEdits = false; return; }
      if (suppressClick || (!editing && (clickEdits || event.target.closest("[data-grid-row]")))) {
        event.preventDefault(); event.stopPropagation();
        if (!suppressClick && clickEdits) beginEdit();
        suppressClick = false; clickEdits = false;
      }
    },true);
    grid.addEventListener("dblclick",event => { if (!event.target.closest("button,a")) {event.preventDefault();beginEdit();} });
    function change(values) {
      if (!host.canEdit()) return host.error("읽기 전용 표입니다.");
      try { Promise.resolve(host.change(values)).catch(error=>host.error(error.message)); } catch (error) {host.error(error.message);}
    }
    grid.addEventListener("keydown",event => {
      if (event.key === 'Escape' && gutterRows.size) {event.preventDefault();event.stopPropagation();setGutterRows([]);return;}
      if (gutterRows.size && (event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'd'
          && !event.isComposing && event.keyCode !== 229 && !event.target.matches('input,select,textarea')) {
        event.preventDefault();event.stopPropagation();duplicateSelectedRows?.();return;
      }
      if (!focus || event.isComposing || event.keyCode === 229 || event.target.closest("button,a")) return;
      if (editing) {
        if (event.key === "Escape" || (event.key === "Enter" && !event.target.matches("select,textarea"))) {event.preventDefault();event.stopPropagation();finishEdit(focus);}
        else if (event.key === "Tab") {
          event.preventDefault();event.stopPropagation();
          const i=rows.indexOf(focus.row)*columns.length+columns.indexOf(focus.column)+(event.shiftKey?-1:1), n=Math.max(0,Math.min(rows.length*columns.length-1,i));
          finishEdit({row:rows[Math.floor(n/columns.length)],column:columns[n%columns.length]});
        }
        return;
      }
      if ((event.ctrlKey || event.metaKey) && ["c","v","x","z","y"].includes(event.key.toLowerCase())) return;
      if (event.key === "Escape") {event.preventDefault();event.stopPropagation();clear();return;}
      if (event.key === "Enter" || event.key === "F2") {event.preventDefault();event.stopPropagation();beginEdit();return;}
      const delta = {ArrowUp:[-1,0],ArrowDown:[1,0],ArrowLeft:[0,-1],ArrowRight:[0,1]}[event.key];
      if (delta || event.key === "Tab") {
        event.preventDefault();event.stopPropagation();
        if (delta) move(...delta,event.shiftKey);
        else {const i=rows.indexOf(focus.row)*columns.length+columns.indexOf(focus.column)+(event.shiftKey?-1:1); const n=Math.max(0,Math.min(rows.length*columns.length-1,i)); select({row:rows[Math.floor(n/columns.length)],column:columns[n%columns.length]});grid.focus();}
      } else if (["Delete","Backspace"].includes(event.key)) {event.preventDefault();event.stopPropagation();change(selected.map(cell=>({...cell,value:""})));}
      else if (event.key.length===1 && !event.ctrlKey && !event.metaKey && !event.altKey) beginEdit();
    },true);
    function copy(event) {
      if (editing || !selected.length) return;
      const rr = rows.filter(row=>selected.some(cell=>cell.row===row)), cc=columns.filter(column=>selected.some(cell=>cell.column===column));
      event.preventDefault();event.stopPropagation();
      try {event.clipboardData.setData("text/plain",toTsv(rr.map(row=>cc.map(column=>host.read({row,column})))));return true;}
      catch (_) {host.error("클립보드에 복사하지 못했습니다. 선택한 셀은 유지됩니다.");return false;}
    }
    grid.addEventListener("copy",copy);
    grid.addEventListener("cut",event => {if (copy(event)) change(selected.map(cell=>({...cell,value:""})));});
    grid.addEventListener("paste",event => {
      if (editing || !focus) return;
      event.preventDefault();event.stopPropagation();
      try {
        const data=parseTsv(event.clipboardData.getData("text/plain")), values=[];
        if(data.length===1 && data[0].length===1) selected.forEach(cell=>values.push({...cell,value:data[0][0]}));
        else {
          const r=rows.indexOf(selected[0].row), c=columns.indexOf(selected[0].column);
          if(r+data.length>rows.length || c+data[0].length>columns.length) throw new Error("붙여넣을 범위가 표의 크기를 넘습니다. 행 또는 열을 먼저 추가하세요.");
          data.forEach((line,i)=>line.forEach((value,j)=>values.push({row:rows[r+i],column:columns[c+j],value})));
        }
        change(values);
      } catch(error) {host.error(error.message);}
    });
    grid.addEventListener("focusout",event=>{if (!grid.contains(event.relatedTarget) && !pointer) {if (grid.isConnected && pendingRestore?.id===host.id) pendingRestore=null;clear();}});
    if (host.id && pendingRestore?.id === host.id) {
      const value = pendingRestore.value;pendingRestore=null;
      queueMicrotask(()=>{if(grid.isConnected && cellAt(value)){select(value);grid.focus({preventScroll:true});}});
    }
  }
  return {rectangle, parseTsv, toTsv, typedValue, edgeScrollDelta, bind, syncGutterRows, clearGutterRows, gutterRowSnapshot};
});

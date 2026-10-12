(function (root, factory) {
  const api = factory(typeof module === 'object' && module.exports ? require('./notion_editor_core') : root.NotionEditorCore);
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.NotionBlockLayout = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function (core) {
  'use strict';
  const bounded = (value, fallback, min, max) => Number.isFinite(Number(value)) ? Math.max(min, Math.min(max, Math.trunc(Number(value)))) : fallback;

  function mount({root, blocks, rows, outline, applyVisual, fullPageId = ''}) {
    const doc = root.ownerDocument, columns = [];
    function region(indices, target, baseIndent = 0, inheritedLayout = null) {
      let activeLayout = null;
      for (const index of indices) {
        const block = blocks[index], entry = outline[index], row = rows[index], props = block.props || {};
        if (fullPageId && block.id !== fullPageId) continue;
        let destination = target, ownLayout = inheritedLayout;
        const group = String(props.layout_group || ''), count = bounded(props.layout_columns, 2, 2, 5);
        const columnIndex = bounded(props.layout_column, 0, 0, count - 1);
        const scope = JSON.stringify([group, props.tab_group || '', props.tab_id || '']);
        if (!fullPageId && group && !(inheritedLayout?.group === group && inheritedLayout.column === columnIndex)) {
          if (!activeLayout || activeLayout.scope !== scope || activeLayout.count !== count) {
            const wrapper = doc.createElement('div'); wrapper.className = 'block-column-layout';
            wrapper.dataset.layoutGroup = group; wrapper.dataset.layoutColumns = String(count);
            wrapper.style.setProperty('--layout-columns', String(count)); wrapper.hidden = true;
            const memberIds = [];
            const elements = Array.from({length:count}, (_, column) => {
              const element = doc.createElement('div'); element.className = 'block-column';
              element.dataset.layoutColumn = String(column); wrapper.appendChild(element);
              columns.push({element, groupId:group, columnIndex:column, columnCount:count,
                parentIndex:entry.parentIndex, memberIds});
              return element;
            });
            target.appendChild(wrapper); activeLayout = {group,scope,count,wrapper,elements,memberIds};
          }
          activeLayout.memberIds.push(block.id);
          activeLayout.wrapper.hidden &&= row.hidden;
          destination = activeLayout.elements[columnIndex]; ownLayout = {group,column:columnIndex};
        } else if (!group) activeLayout = null;
        const indent = fullPageId ? 0 : Math.max(0, entry.visualIndent - baseIndent);
        const hasChildren = !fullPageId && entry.childIndices.length > 0;
        if (!fullPageId && (hasChildren || block.type === 'callout')) {
          const branch = doc.createElement('div'), children = doc.createElement('div');
          branch.className = block.type === 'callout' ? 'block-callout-group' : 'block-branch';
          branch.dataset.branchId = block.id;
          if (block.type === 'callout') branch.dataset.calloutId = block.id;
          branch.style.setProperty(block.type === 'callout' ? '--callout-indent' : '--branch-indent', String(indent));
          branch.hidden = row.hidden; applyVisual(branch, block);
          children.className = block.type === 'callout' ? 'block-callout-children' : 'block-branch-children';
          const container = core.isLayoutContainer(block);
          const step = block.type === 'tabs' || container ? 0 : 1;
          children.style.setProperty('--branch-step', String(step));
          row.style.setProperty('--block-indent', '0');
          if (!container) branch.appendChild(row);
          branch.appendChild(children); destination.appendChild(branch);
          region(entry.childIndices, children, entry.visualIndent + step, ownLayout);
        } else {
          row.style.setProperty('--block-indent', String(indent)); destination.appendChild(row);
        }
      }
    }
    const roots = fullPageId ? blocks.map((_,i)=>i).filter(i=>blocks[i].id===fullPageId)
      : outline.map((entry,i)=>entry.parentIndex < 0 ? i : -1).filter(i=>i>=0);
    region(roots, root);
    return {columns};
  }
  return {mount};
});

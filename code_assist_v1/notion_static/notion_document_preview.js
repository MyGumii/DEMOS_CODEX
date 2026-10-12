(function (root, factory) {
  const api = factory(typeof module === 'object' && module.exports ? require('./notion_editor_core.js') : root.NotionEditorCore);
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.NotionDocumentPreview = api;
})(typeof globalThis === 'object' ? globalThis : this, function (editorCore) {
  'use strict';
  const databaseTypes = new Set(['table', 'board', 'list', 'gallery', 'calendar', 'timeline', 'chart', 'dashboard', 'form', 'feed', 'map']);
  const array = value => Array.isArray(value) ? value : [];
  const scalar = value => typeof value === 'string' || typeof value === 'number' ? String(value) : '';
  const escape = value => scalar(value).replace(/[&<>"']/g, c => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[c]));
  const integer = (value, min, max) => Math.max(min, Math.min(max, Math.trunc(Number(value)) || min));

  function render(data, options = {}) {
    const references = data.references || {}, databases = new Map(array(data.databases).map(db => [String(db.id), db]));
    const text = value => escape(value).replace(/\n/g, '<br>');
    const rich = block => block.props?.rich_text_html && options.richHtml
      ? options.richHtml(block, data) : text(block.content || '');
    const reference = (id, fallback) => {
      const page = references[id];
      const allowed = data.contract === 'code_assist.notion.search_preview.v1' ? Boolean(page) : true;
      const title = allowed ? (page ? page.title || '제목 없음' : fallback || '제목 없음') : '접근할 수 없는 페이지';
      return `<div class="preview-page-link">${options.iconHtml?.(page || {}) || ''}<span>${text(title)}</span></div>`;
    };
    function cell(value, prop, db) {
      if (prop.type === 'person') return array(Array.isArray(value) ? value : value ? [value] : [])
        .map(id => text(options.personLabel?.(id) || '멤버')).join(', ');
      if (prop.type === 'relation') return array(value).map(id => text(db?.relation_labels?.[prop.id]?.[id] || '연결된 페이지')).join(', ');
      if (prop.type === 'files') return array(value).map(file => text(file?.name || file?.original_name || '첨부파일')).join(', ');
      if (typeof value === 'boolean') return `<span aria-label="${value ? '체크됨' : '체크 안 됨'}">${value ? '☑' : '☐'}</span>`;
      if (Array.isArray(value)) return value.map(item => text(item?.name || scalar(item))).join(', ');
      if (value && typeof value === 'object') return [value.start, value.end].filter(Boolean).map(text).join(' → ');
      return text(value);
    }
    function database(original) {
      const db = databases.get(String(original.props?.database_id || original.id || ''));
      const block = options.databaseBlock ? options.databaseBlock(original, db) : original;
      const props = array(block.props?.properties), rows = options.databaseRows
        ? options.databaseRows(block).map(entry => entry.item) : array(block.props?.rows);
      const visible = props.filter(prop => block.props?.property_visibility?.[prop.id] !== false);
      const title = `<div class="preview-database-title">${text(block.content || db?.title || '데이터베이스')}</div>`;
      const header = `<thead><tr>${visible.map(prop => `<th scope="col">${text(prop.name || '')}</th>`).join('')}</tr></thead>`;
      const body = rows.map(row => `<tr>${visible.map(prop => `<td>${cell(row[prop.id], prop, db)}</td>`).join('')}</tr>`).join('');
      return `${title}<div class="preview-table-scroll"><table>${header}<tbody>${body}</tbody></table></div>`;
    }
    function blockHtml(block, number, children = '') {
      const type = scalar(block.type) || 'paragraph', props = block.props || {};
      if (databaseTypes.has(type)) return database(block);
      if (type === 'divider') return '<hr>';
      if (type === 'simple_table') return `<div class="preview-table-scroll"><table><tbody>${array(props.rows).map((row, r) => `<tr>${array(row).map((value, c) => {
        const tag = props.header_row && r === 0 || props.header_column && c === 0 ? 'th' : 'td';
        return `<${tag}>${text(value)}</${tag}>`;
      }).join('')}</tr>`).join('')}</tbody></table></div>`;
      if (/^(?:toggle_)?heading[1234]$/.test(type)) {
        const level = Number(type.slice(-1)) + 2;
        return `<h${level}>${type.startsWith('toggle_') ? `<span aria-hidden="true">${props.open === false ? '▸' : '▾'} </span>` : ''}${rich(block)}</h${level}>`;
      }
      if (type === 'bullet') return `<ul><li>${rich(block)}</li></ul>`;
      if (type === 'numbered') return `<ol start="${number}"><li>${rich(block)}</li></ol>`;
      if (type === 'quote') return `<blockquote>${rich(block)}</blockquote>`;
      if (type === 'callout') return `<div class="preview-callout"><span class="preview-callout-icon" aria-hidden="true">${text(props.icon || '💡')}</span><div class="preview-callout-body"><div>${rich(block)}</div>${children ? `<div class="preview-children">${children}</div>` : ''}</div></div>`;
      if (type === 'todo') return `<div class="preview-todo"><span aria-label="${props.checked ? '완료' : '미완료'}">${props.checked ? '☑' : '☐'}</span><div${props.checked ? ' class="completed"' : ''}>${rich(block)}</div></div>`;
      if (type === 'toggle') return `<p><span aria-hidden="true">${props.open === false ? '▸' : '▾'} </span>${rich(block)}</p>`;
      if (type === 'code') return `<figure><pre><code>${escape(block.content || '')}</code></pre>${props.caption ? `<figcaption>${text(props.caption)}</figcaption>` : ''}</figure>`;
      if (type === 'equation') return `<div class="preview-equation">${options.mathHtml?.(block.content || '') || text(block.content || '')}</div>`;
      if (type === 'child_page' || type === 'link_to_page') return reference(props.page_id, block.content || props.title);
      if (type === 'table_of_contents') return `<nav aria-label="문서 목차">${array(data.blocks).filter(item => /^(?:toggle_)?heading[1234]$/.test(item.type)).map(item => `<div>${text(item.content)}</div>`).join('')}</nav>`;
      if (type === 'breadcrumb') return reference(data.page?.parent_id, data.page?.title);
      if (type === 'tabs') return `<div class="preview-tabs">${array(props.tabs).map(tab => `<span${tab.id === props.active_tab_id ? ' class="active"' : ''}>${text(tab.name)}</span>`).join('')}</div>`;
      if (type === 'button') return `<span class="preview-button-label">${text(props.label || '버튼')}</span>`;
      if (type === 'ai_notes') {
        const result = props.result || {};
        return [['요약', result.summary], ['메모', props.notes], ['회의록 원문', props.transcript], ['번역', props.translation_text]]
          .filter(([, value]) => value).map(([label, value]) => `<section><h4>${label}</h4><p>${text(value)}</p></section>`).join('')
          + (array(result.decisions).length ? `<h4>결정 사항</h4><ul>${result.decisions.map(item => `<li>${text(item.text)}</li>`).join('')}</ul>` : '')
          + (array(result.action_items).length ? `<h4>할 일</h4><ul>${result.action_items.map(item => `<li>${text(item.task)}</li>`).join('')}</ul>` : '');
      }
      if (['image', 'file', 'video', 'audio', 'pdf'].includes(type)) {
        const name = props.name || props.original_name || block.content || '첨부파일';
        const url = props.file_id && options.fileUrl?.(props.file_id);
        const media = type === 'image' && typeof url === 'string' && url.startsWith('/api/notion/')
          ? `<img src="${escape(url)}" alt="${escape(name)}" loading="lazy">` : `<div class="preview-attachment">${text(name)}</div>`;
        return `<figure>${media}${block.content ? `<figcaption>${rich(block)}</figcaption>` : ''}${props.comment ? `<figcaption>${text(props.comment)}</figcaption>` : ''}</figure>`;
      }
      if (type === 'embed' && props.embed_mode === 'html') return `<div class="preview-attachment">HTML 임베드</div>${block.content ? rich(block) : ''}`;
      if (type === 'bookmark' || type === 'embed') return `<div class="preview-bookmark">${rich(block)}<small>${text(props.url || props.link_url || '')}</small></div>`;
      return `<p>${rich(block) || '<br>'}</p>`;
    }
    const blocks = array(data.blocks), tabs = new Map(blocks.filter(block => block.type === 'tabs').map(block => [block.id, block]));
    const parts = [], counters = Array(33).fill(0), outline = editorCore.blockOutline(blocks), callouts = new Map();
    let layout = null;
    function flushLayout() {
      if (layout) parts.push(layout);
      layout = null;
    }
    for (const [index, block] of blocks.entries()) {
      const props = block.props || {}, structure = outline[index], indent = structure.indent, tab = tabs.get(props.tab_group);
      if (structure.collapsed || tab && props.tab_id !== (tab.props?.active_tab_id || tab.props?.tabs?.[0]?.id)) continue;
      if (block.type === 'numbered') counters[indent] += 1; else counters[indent] = 0;
      counters.fill(0, indent + 1);
      let target = parts;
      if (props.layout_group) {
        const count = integer(props.layout_columns || 2, 2, 5), column = integer(props.layout_column, 0, count - 1);
        if (!layout || layout.id !== props.layout_group || layout.columns.length !== count) {
          flushLayout(); layout = {id: props.layout_group, columns: Array.from({length: count}, () => [])};
        }
        target = layout.columns[column];
      } else flushLayout();
      const owner = callouts.get(structure.calloutIndex);
      const node = {block, number: counters[indent], children: [], visualIndent: structure.visualIndent,
        indent: owner ? Math.max(0, structure.visualIndent - owner.visualIndent - 1) : structure.visualIndent};
      (owner ? owner.children : target).push(node);
      if (block.type === 'callout') callouts.set(index, node);
    }
    flushLayout();
    function renderNodes(nodes) {
      return nodes.map(node => node.columns
        ? `<div class="preview-columns" style="--preview-columns:${node.columns.length}">${node.columns.map(column => `<div>${renderNodes(column)}</div>`).join('')}</div>`
        : `<div class="preview-block${node.block.type === 'callout' ? ' preview-callout-group' : ''}" data-block-type="${escape(node.block.type || 'paragraph')}" style="--preview-indent:${node.indent}">${blockHtml(node.block, node.number, renderNodes(node.children))}</div>`).join('');
    }
    return `<div class="document-preview">${renderNodes(parts) || '<p class="preview-empty">내용이 없는 페이지입니다.</p>'}</div>`;
  }
  return {render};
});

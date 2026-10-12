(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  root.NotionSidebarPageGroups = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  const expandedGroups = new Set();

  function plan(pages, {limit = Infinity, tree = false, expanded = false, activeId = ''} = {}) {
    const byId = new Map(pages.map(page => [page.id, page]));
    const roots = new Map();
    function rootOf(page) {
      if (!tree) return page.id;
      const chain = [], seen = new Set();
      while (page && !roots.has(page.id)) {
        if (seen.has(page.id)) {
          const cycle = chain.slice(chain.indexOf(page.id));
          const root = [...cycle].sort()[0];
          chain.forEach(id => roots.set(id, root));
          return root;
        }
        seen.add(page.id); chain.push(page.id);
        const parent = byId.get(page.parent_id);
        if (!parent || parent.id === page.id) break;
        page = parent;
      }
      const root = roots.get(page.id) || page.id;
      chain.forEach(id => roots.set(id, root));
      return root;
    }
    pages.forEach(rootOf);
    const groups = [...new Set(pages.map(rootOf))];
    if (tree) groups.sort((a, b) => {
      const left = byId.get(a), right = byId.get(b);
      return (left.position || 0) - (right.position || 0) || String(left.title).localeCompare(String(right.title));
    });
    const count = Number.isFinite(limit) ? Math.max(1, Math.floor(limit)) : groups.length;
    const selected = new Set(groups.slice(0, count));
    if (byId.has(activeId)) selected.add(rootOf(byId.get(activeId)));
    const compact = pages.filter(page => selected.has(rootOf(page)));
    return {items: expanded ? pages : compact, canExpand: compact.length < pages.length,
      hiddenCount: pages.length - compact.length};
  }

  function render(host) {
    const scope = host.scope(), key = JSON.stringify([scope, host.root.id]);
    const expanded = expandedGroups.has(key);
    const result = plan(host.pages, {...host.options, limit: host.limit, activeId: host.activeId,
      expanded: expanded || !host.toggle});
    host.draw(host.root, result.items, host.options);
    const toggle = host.toggle;
    if (!toggle) return;
    toggle.hidden = !result.canExpand;
    toggle.textContent = expanded ? '간략히 보기' : `더 보기 (${result.hiddenCount})`;
    toggle.setAttribute('aria-expanded', String(expanded));
    toggle.setAttribute('aria-label', expanded ? `${host.label} 간략히 보기` : `${host.label} ${host.pages.length}개 모두 보기`);
    toggle.onclick = () => {
      if (host.scope() !== scope || host.locked()) return;
      if (expanded) expandedGroups.delete(key); else expandedGroups.add(key);
      render(host);
      if (!toggle.hidden) {
        toggle.focus({preventScroll: true});
        toggle.scrollIntoView({block: 'nearest', inline: 'nearest'});
      }
    };
  }
  return {plan, render};
});

/* Presentation order observed in the Korean Notion menu, refreshed 2026-10-01.
 * Storage type IDs and excluded database capabilities remain unchanged. */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  root.NotionSlashMenu = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";
  const groups = [
    ["추천", ["ai_notes", "html_embed", "table", "heading1"]],
    ["기본 블록", ["paragraph", "heading1", "heading2", "heading3", "heading4", "bullet", "numbered", "todo", "toggle", "child_page", "callout", "quote", "simple_table", "divider", "link_to_page"]],
    ["미디어", ["image", "video", "audio", "code", "file", "bookmark"]],
    ["데이터베이스", ["table", "board", "gallery", "list", "calendar", "timeline", "linked_database"]],
    ["고급 블록", ["table_of_contents", "equation", "button", "breadcrumb", "tabs", "synced_block", "toggle_heading1", "toggle_heading2", "toggle_heading3", "toggle_heading4", "layout_columns_2", "layout_columns_3", "layout_columns_4", "layout_columns_5", "ai_notes"]],
    ["임베드", ["embed", "html_embed", "pdf"]],
  ];
  const labels = { heading1: "제목1", heading2: "제목2", heading3: "제목3", heading4: "제목4", simple_table: "표", list: "리스트 보기", equation: "수학 공식 블록", breadcrumb: "이동 경로", html_embed: "HTML", toggle_heading1: "토글 제목1", toggle_heading2: "토글 제목2", toggle_heading3: "토글 제목3", toggle_heading4: "토글 제목4" };
  const extra = [
    { type: "linked_database", label: "데이터 소스 보기 연결", detail: "데이터베이스", keywords: ["linked", "database", "연결", "데이터 소스"] },
    { type: "html_embed", label: "HTML", keywords: ["html", "임베드", "embed", "업로드"] },
    { type: "layout_columns_2", label: "2개의 열", keywords: ["2 columns", "two columns", "2열", "열"] },
    { type: "layout_columns_3", label: "3개의 열", keywords: ["3 columns", "three columns", "3열", "열"] },
    { type: "layout_columns_4", label: "4개의 열", keywords: ["4 columns", "four columns", "4열", "열"] },
    { type: "layout_columns_5", label: "5개의 열", keywords: ["5 columns", "five columns", "5열", "열"] },
  ];
  function definitions(coreDefinitions) {
    const byType = new Map([...coreDefinitions, ...extra].map(item => [item.type, item]));
    return groups.flatMap(([group, types]) => types.filter(type => byType.has(type)).map(type => ({
      ...byType.get(type), group, label: labels[type] || byType.get(type).label,
      // Keep the existing editor label searchable when its presentation changes.
      keywords: [...new Set([...(byType.get(type).keywords || []), byType.get(type).label].filter(Boolean))],
      shortcut: type === "ai_notes" ? "" : (/^toggle_heading/.test(type) ? `${"#".repeat(Number(type.slice(-1)))} >` : (byType.get(type).shortcut || "")),
      detail: group === "추천" && type === "html_embed" ? "임베드"
        : group === "추천" && type === "table" ? "데이터베이스" : "",
      badge: type === "html_embed" && group === "추천" ? "New" : "",
    })));
  }
  function searchText(item) {
    return [item.type, item.label, item.detail, item.shortcut, ...(item.keywords || [])].join(" ").toLowerCase();
  }
  function matches(text, query) {
    const q = String(query || "").trim().replace(/^\//, "").toLowerCase();
    return !q || text.includes(q) || text.replace(/\s/g, "").includes(q.replace(/\s/g, ""));
  }
  const MAX_PINS = 8;
  function preferences(storage, user, workspace, now = Date.now()) {
    const key = `notion:slash:v1:${encodeURIComponent(user || "")}:${encodeURIComponent(workspace || "personal")}`;
    let raw = {};
    try { raw = JSON.parse(storage?.getItem(key) || "{}"); } catch (_) { /* Private mode or malformed preferences. */ }
    const valid = value => typeof value === "string" && /^[a-z][a-z0-9_]{0,60}$/.test(value);
    const value = {
      mode: raw?.mode === "recent" ? "recent" : "frequent",
      pins: [...new Set((Array.isArray(raw?.pins) ? raw.pins : []).filter(valid))].slice(0, MAX_PINS),
      events: (Array.isArray(raw?.events) ? raw.events : []).filter(item => valid(item?.type) && Number.isFinite(item.at) && item.at <= now && item.at > now - 30 * 86400000).slice(-300),
    };
    const save = () => { try { if (!user || !storage) return false; storage.setItem(key, JSON.stringify(value)); return true; } catch (_) { return false; } };
    return { value, save, toggle(type) {
      if (!valid(type)) return false;
      if (value.pins.includes(type)) value.pins = value.pins.filter(item => item !== type);
      else { if (value.pins.length >= MAX_PINS) return false; value.pins.push(type); }
      return true;
    }, record(type) { if (valid(type)) { value.events.push({ type, at: now }); value.events = value.events.slice(-300); return save(); } return false; } };
  }
  function personalized(items, prefs) {
    const byType = new Map();
    items.forEach(item => { if (!byType.has(item.type)) byType.set(item.type, item); });
    const pins = prefs.pins.filter(type => byType.has(type));
    const stats = new Map();
    prefs.events.forEach(({type, at}) => { const prior = stats.get(type) || {count: 0, at: 0}; stats.set(type, {count: prior.count + 1, at: Math.max(prior.at, at)}); });
    const ranked = [...stats.keys()].filter(type => byType.has(type) && !pins.includes(type)).sort((a, b) =>
      (prefs.mode === "recent" ? stats.get(b).at - stats.get(a).at : stats.get(b).count - stats.get(a).count) || stats.get(b).at - stats.get(a).at || a.localeCompare(b));
    const recommended = [...new Set([...pins, ...ranked, ...items.filter(item => item.group === "추천").map(item => item.type)])].slice(0, 6);
    // Preserve previous custom ordering without exposing a separate group in the Notion-style menu.
    return [...recommended.map(type => ({...byType.get(type), group: "추천"})), ...items.filter(item => item.group !== "추천")];
  }
  // Original line icons, not platform-dependent emoji or extracted vendor SVGs.
  function icon(type) {
    const paths = {
      paragraph: '<path d="M5 4h14M12 4v16M8 20h8"/>',
      ai_notes: '<path d="M13 21H5V3h12v7M8 7h6M8 11h3M15 15v-2a2 2 0 0 1 4 0v2a2 2 0 0 1-4 0ZM13 15a4 4 0 0 0 8 0M17 19v3"/>',
      html_embed: '<path d="M5 3h9l4 4v4M14 3v4h4M5 3v18h7M8 10h6M8 13h3"/><path d="M14 15h6v6m0-6-7 7"/>',
      embed: '<path d="m8 8-5 4 5 4m8-8 5 4-5 4m-3-11-2 14"/>',
      bullet: '<path d="M9 6h12M9 12h12M9 18h12"/><circle cx="4" cy="6" r="1"/><circle cx="4" cy="12" r="1"/><circle cx="4" cy="18" r="1"/>',
      numbered: '<path d="M10 6h11M10 12h11M10 18h11M3 4h2v5M3 9h4M3 14q4-2 4 1l-4 5h4"/>',
      todo: '<rect x="3" y="3" width="18" height="18" rx="3"/><path d="m7 12 3 3 7-7"/>',
      toggle: '<path d="m8 5 9 7-9 7Z"/>',
      child_page: '<path d="M5 3h9l5 5v13H5ZM14 3v6h5M8 13h8M8 17h8"/>',
      callout: '<path d="M9 17h6v4H9ZM9 17c0-4-4-4-4-8a7 7 0 0 1 14 0c0 4-4 4-4 8M9 19h6"/>',
      quote: '<path d="M4 4v16M9 6h12M9 12h10M9 18h12"/>',
      simple_table: '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18M3 15h18M9 3v18M15 3v18"/>',
      divider: '<path d="M3 12h18"/>',
      link_to_page: '<path d="M9 7V3h10v18H9v-4M3 12h10m-4-4 4 4-4 4"/>',
      image: '<rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8" cy="8" r="2"/><path d="m3 18 6-6 4 4 3-3 5 5"/>',
      video: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="m10 8 6 4-6 4Z"/>',
      audio: '<path d="M10 18V5l10-2v13M10 9l10-2"/><ellipse cx="6" cy="18" rx="4" ry="3"/><ellipse cx="16" cy="16" rx="4" ry="3"/>',
      code: '<path d="m8 6-6 6 6 6m8-12 6 6-6 6M14 3l-4 18"/>',
      file: '<path d="M8 13V6a4 4 0 0 1 8 0v12a6 6 0 0 1-12 0V8m8-2v12a2 2 0 0 1-4 0v-5"/>',
      bookmark: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 9h18M6 6.5h1M9 6.5h1M6 13h12M6 16h8"/>',
      table: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 9h18M3 14h18M10 9v11"/>',
      board: '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M9 3v18M15 3v18M5 7h2M11 7h2M17 7h2M5 10h2M11 10h2M5 13h2"/>',
      gallery: '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>',
      list: '<path d="M3 5h18M3 12h18M3 19h18"/>',
      calendar: '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M7 2v6M17 2v6M3 10h18M7 14h2M12 14h2M7 18h2"/>',
      linked_database: '<rect x="3" y="4" width="18" height="16" rx="1.5"/><path d="M3 9h18M3 14h18M10 9v11M17 9v5M15 18h5m-2-2 2 2-2 2"/>',
      timeline: '<path d="M4 3v18M9 5h12M7 12h9M12 19h9"/>',
      table_of_contents: '<path d="M3 5h18M7 10h14M7 15h14M3 20h18"/>',
      equation: '<path d="M20 4H5l7 8-7 8h15"/>',
      button: '<rect x="3" y="6" width="18" height="12" rx="4"/><path d="M8 12h8"/>',
      breadcrumb: '<path d="m3 7 5 5-5 5m8-10 5 5-5 5m8-10 3 5-3 5"/>',
      synced_block: '<path d="M5 7a8 8 0 0 1 14 1M19 3v5h-5M19 17A8 8 0 0 1 5 16M5 21v-5h5"/>',
      layout_columns_2: '<rect x="3" y="3" width="18" height="18" rx="1"/><path d="M12 3v18"/>',
      layout_columns_3: '<rect x="3" y="3" width="18" height="18" rx="1"/><path d="M9 3v18M15 3v18"/>',
      tabs: '<path d="M3 21V3h7v4h11v14ZM3 7h18"/>',
    };
    let body = paths[type] || paths.child_page;
    if (/^(toggle_)?heading[1234]$/.test(type)) body = `${type.startsWith("toggle_") ? '<path d="m1 8 3 4-3 4"/>' : ""}<path d="M6 5v14M15 5v14M6 12h9"/><text x="17" y="20" fill="currentColor" stroke="none" font-size="10" font-family="sans-serif">${type.slice(-1)}</text>`;
    if (/^layout_columns_[45]$/.test(type)) body = `<rect x="2" y="3" width="20" height="18" rx="1"/>${Array.from({length:Number(type.slice(-1))-1}, (_, i) => `<path d="M${2 + (i + 1) * 20 / Number(type.slice(-1))} 3v18"/>`).join("")}`;
    return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">${body}</svg>`;
  }
  function trigger(text, offset) {
    const source = String(text || "");
    const end = Math.max(0, Math.min(source.length, Number(offset) || 0));
    const match = source.slice(0, end).match(/(?:^|\s)\/([^/\r\n]{0,100})$/);
    if (!match) return null;
    const query = match[1];
    return {start: end - query.length - 1, end, query, text: `/${query}`};
  }
  return Object.freeze({ definitions, searchText, matches, icon, preferences, personalized, trigger, MAX_PINS });
});

(function (root, factory) {
  const create = factory();
  if (typeof module === "object" && module.exports) module.exports = create;
  else root.NotionMeetings = create();
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";
  return function createMeetings(options = {}) {
    let host, visible = false, generation = 0, entrySequence = 0, timer = null, creating = false;
    let query = "", items = [], loading = false, hasMore = false, listKey = "", listQuery = "";
    let viewKey = "";
    const $ = id => document.getElementById(options.ids?.[id] || id);
    const node = (tag, text, className) => {
      const element = document.createElement(tag);
      if (text !== undefined) element.textContent = text;
      if (className) element.className = className;
      return element;
    };
    function render() {
      const key = host?.key(), renderedQuery = query;
      $("meetingList").replaceChildren(...items.map(item => {
        const button = node("button", undefined, "meeting-row");
        button.type = "button";
        const icon = node("span", undefined, "meeting-icon");
        icon.innerHTML = host.icon(item);
        const label = node("span", undefined, "meeting-row-label");
        label.append(node("strong", item.title || "제목 없음"), node("small", `${item.note_count}개 노트 · ${host.relative(item.updated_at)}`));
        button.append(icon, label);
        button.addEventListener("click", () => {
          if (visible && key === host.key() && renderedQuery === query && items.includes(item)) return host.openPage(item);
        });
        return button;
      }));
    }
    async function load(more = false) {
      if (!visible || viewKey !== host.key() || (more && (loading || !hasMore || listKey !== host.key() || listQuery !== query))) return;
      clearTimeout(timer); timer = null;
      const request = ++generation, key = host.key();
      const requestedQuery = query, offset = more ? items.length : 0;
      loading = true;
      if (!more) { items = []; hasMore = false; render(); $("meetingListMore").hidden = true; }
      $("meetingListStatus").textContent = "회의 노트를 불러오는 중…";
      $("meetingListMore").disabled = true;
      try {
        const data = await host.list({query: requestedQuery, offset});
        if (!visible || generation !== request || key !== host.key()) return;
        items = more ? [...items, ...(data.items || [])] : data.items || [];
        listKey = key; listQuery = requestedQuery; hasMore = Boolean(data.has_more);
        render();
        $("meetingListMore").hidden = !hasMore;
        $("meetingListStatus").textContent = data.degraded
          ? "일부 회의 노트를 불러오지 못했습니다. 새로고침해 다시 확인하세요."
          : items.length ? `${data.count}개 문서` : query ? "검색 결과가 없습니다." : "아직 회의 노트가 없습니다. 새 노트를 작성해 보세요.";
      } catch (error) {
        if (!visible || generation !== request || key !== host.key()) return;
        $("meetingListStatus").textContent = `회의 노트를 불러오지 못했습니다: ${error.message}`;
        $("meetingListMore").hidden = true;
      } finally {
        if (generation === request) { loading = false; $("meetingListMore").disabled = false; }
      }
    }
    async function open(push = true) {
      if (!host) return false;
      const entry = ++entrySequence, key = host.key();
      const currentEntry = () => entry === entrySequence && key === host.key();
      if (!options.sidebar && !(await host.enter(currentEntry))) return false;
      if (!currentEntry()) return false;
      visible = true; viewKey = host.key();
      query = ""; $("meetingSearch").value = "";
      $("meetingsView").hidden = false;
      $("meetingNewBtn").hidden = !host.canCreate();
      if (!options.sidebar) {
        host.route(push);
        $("meetingsTitle").focus({preventScroll: true});
      }
      await load();
      return true;
    }
    function leave() {
      visible = false; entrySequence += 1; generation += 1; clearTimeout(timer);
      if ($("meetingsView")) $("meetingsView").hidden = true;
      items = []; query = ""; loading = false; hasMore = false; listKey = ""; listQuery = ""; viewKey = "";
      render();
    }
    function init(bindings) {
      host = bindings;
      $("meetingSearch").addEventListener("input", event => {
        if (!visible) return;
        query = event.target.value.trim();
        generation += 1;
        clearTimeout(timer);
        items = []; hasMore = false; loading = false; render();
        $("meetingListMore").hidden = true;
        $("meetingListMore").disabled = true;
        $("meetingListStatus").textContent = "회의 노트를 검색하는 중…";
        timer = setTimeout(() => load(), 180);
      });
      $("meetingListMore").addEventListener("click", () => load(true));
      $("meetingRefreshBtn").addEventListener("click", () => load());
      $("meetingNewBtn").addEventListener("click", async () => {
        if (!visible || viewKey !== host.key() || creating || !host.canCreate()) return;
        creating = true; $("meetingNewBtn").disabled = true;
        try { await host.create(); if (options.sidebar && visible && viewKey === host.key()) void load(); }
        finally { creating = false; $("meetingNewBtn").disabled = false; }
      });
    }
    const showSidebar = () => options.sidebar && (!visible || viewKey !== host.key()) ? open(false) : Promise.resolve(false);
    return {init, open, leave, showSidebar, create: createMeetings};
  };
});

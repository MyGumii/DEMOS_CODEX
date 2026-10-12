(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  root.NotionPersonalWorkspace = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";
  function create(host) {
    let items = new Map(), loadSequence = 0;
    const icons = new Map(), files = new WeakMap();
    const active = () => Boolean(host.active());
    const get = owner => active() ? items.get(owner) : null;
    async function refresh() {
      if (!active()) return;
      const scope = host.scope(), ticket = ++loadSequence;
      try {
        const result = await host.api("/api/notion/personal-workspaces");
        if (!active() || scope !== host.scope() || ticket !== loadSequence) return;
        items = new Map((result.items || []).map(item => [item.workspace.owner_user_id, item]));
        host.changed();
      } catch (error) {
        if (!active() || scope !== host.scope() || ticket !== loadSequence) return;
        // Older servers expose no endpoint. A settings screen must not offer an
        // unverified write; a later opening can retry a transient failure.
        items = new Map(); host.changed();
      }
    }
    async function save(owner, value, file = null) {
      const scope = host.scope(), subject = host.subject();
      const same = () => active() && scope === host.scope() && host.owner() === owner;
      if (!same() || owner !== subject) throw new Error("내 워크스페이스에서 아이콘을 변경해 주세요.");
      const key = JSON.stringify([subject, owner, file ? "image" : value]);
      let intents = icons;
      if (file) { intents = files.get(file); if (!intents) { intents = new Map(); files.set(file, intents); } }
      let intent = intents.get(key);
      if (!intent) { intent = {}; intents.set(key, intent); }
      if (intent.inFlight) return intent.inFlight;
      const path = `/api/notion/personal-workspaces/${encodeURIComponent(owner)}`;
      intent.inFlight = (async () => {
        try {
          if (!intent.request) {
            const mime = file ? await host.validateImage(file) : null;
            const current = await host.api(path + "/appearance");
            if (!current.capabilities?.workspace_appearance || (file && !current.capabilities.workspace_image) || current.workspace?.access?.role !== "owner") throw new Error("현재 이 워크스페이스의 아이콘을 변경할 수 없습니다.");
            intent.request = {base_version: current.workspace.version, operation_id: host.operationId()};
            if (file) intent.mime = mime;
            else intent.request.icon = value;
          }
          if (!same()) throw new Error("아이콘을 변경하던 워크스페이스에서 다시 시도해 주세요.");
          if (file) {
            const response = await host.fetch(path + "/icon-image", {method: "POST", body: file, headers: {
              "Content-Type": intent.mime, "X-Notion-Base-Version": String(intent.request.base_version),
              "X-Notion-Operation-Id": intent.request.operation_id,
            }});
            if (!response.ok) {
              const data = await response.json().catch(() => ({}));
              throw Object.assign(new Error(data.message || "이미지를 저장하지 못했습니다."), {status: response.status});
            }
          } else await host.api(path + "/appearance", {method: "PATCH", body: intent.request});
          const current = await host.api(path + "/appearance");
          intents.delete(key);
          if (!same()) return;
          loadSequence++; // An earlier list response cannot overwrite this commit.
          items.set(owner, current); host.changed();
          host.status("워크스페이스 아이콘을 저장했습니다.", "ok");
        } catch (error) {
          if (error.status >= 400 && error.status < 500) intents.delete(key);
          if (error.status === 409) throw new Error("아이콘이 변경되었습니다. 확인 후 다시 적용해 주세요.");
          throw error;
        } finally { intent.inFlight = null; }
      })();
      return intent.inFlight;
    }
    return {get, refresh, saveIcon: (owner, icon) => save(owner, icon), saveImage: (owner, file) => save(owner, null, file)};
  }
  return {create};
});

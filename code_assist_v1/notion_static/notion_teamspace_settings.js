/* Team metadata requests retain the same intent after an uncertain response. */
(function (root) {
  "use strict";
  function create(host) {
    const intents = new Map();
    async function open(workspace, teamspace) {
      const scope = host.scope(), key = JSON.stringify([host.actor(), workspace, teamspace]);
      const current = () => scope === host.scope();
      const path = `/api/notion/workspaces/${encodeURIComponent(workspace)}/teamspaces/${encodeURIComponent(teamspace)}`;
      function validate(data) {
        if (data?.teamspace?.id !== teamspace || data.teamspace.workspace_id !== workspace
            || !Number.isInteger(data.workspace_version) || !Array.isArray(data.members)) {
          throw new Error("팀스페이스 정보를 확인하지 못했습니다.");
        }
        return data;
      }
      let data = validate(await host.api(path));
      if (!current()) return null;
      return {
        get data() { return data; },
        get pending() { return intents.get(key)?.request || null; },
        current,
        async save(name) {
          if (!current()) return null;
          let intent = intents.get(key);
          if (intent?.running) return intent.running;
          if (!intent) {
            if (!data.capabilities?.rename) throw new Error("팀스페이스 이름을 변경할 권한이 없습니다.");
            name = String(name || "").trim();
            if (!name) throw new Error("팀스페이스 이름을 입력해 주세요.");
            intent = {request: {name, base_version: data.workspace_version, operation_id: host.operationId()}};
            intents.set(key, intent);
          }
          intent.running = (async () => {
            let committed = false;
            try {
              await host.api(path, {method: "PATCH", body: intent.request});
              committed = true;
              // An exact retry can return an old receipt; read the current value.
              const fresh = validate(await host.api(path));
              intents.delete(key);
              if (!current()) return null;
              data = fresh;
              return fresh;
            } catch (error) {
              if (!committed && error.status >= 400 && error.status < 500) intents.delete(key);
              if (error.status === 400 && /teamspace name already exists/.test(error.message)) {
                throw new Error("같은 이름의 팀스페이스가 있습니다. 다른 이름을 입력해 주세요.");
              }
              if (error.status === 409) {
                // Keep the user's input, but require a fresh dialog before resubmitting.
                data = {...data, capabilities: {rename: false}};
                throw new Error("팀스페이스가 변경되었습니다. 창을 다시 열어 최신 정보를 확인해 주세요.");
              }
              throw error;
            } finally { intent.running = null; }
          })();
          return intent.running;
        },
      };
    }
    return {open};
  }
  root.NotionTeamspaceSettings = {create};
})(typeof window === "object" ? window : globalThis);

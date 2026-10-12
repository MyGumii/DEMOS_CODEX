(function(root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.NotionPageCopy = api;
})(typeof globalThis === "object" ? globalThis : this, function() {
  "use strict";

  const PREFIX = "notion.page-copy.v1:";
  const CONTRACT = "code_assist.notion.canonical_page_copy.v1";
  const fields = ["operation_id", "expected_document_sequence", "expected_workspace_epoch", "expected_document_epoch"];
  const clone = value => JSON.parse(JSON.stringify(value));
  const id = value => typeof value === "string" && value.length > 0 && value.length <= 256;
  const invalid = () => new Error("저장된 복제 요청을 확인할 수 없습니다. 새 복제를 시작하지 않았습니다.");

  function scopePrefix(scope) {
    if (!id(scope?.userId) || !id(scope?.workspaceId)) throw invalid();
    return PREFIX + [scope.userId, scope.workspaceId].map(encodeURIComponent).join(":") + ":";
  }
  function keyFor(scope) {
    if (!id(scope?.pageId)) throw invalid();
    return scopePrefix(scope) + encodeURIComponent(scope.pageId);
  }
  function validRequest(request) {
    return request && Object.keys(request).length === fields.length
      && id(request.operation_id) && fields.slice(1).every(field => Number.isSafeInteger(request[field]) && request[field] > 0);
  }
  function validate(record, scope) {
    if (!record || record.version !== 1 || record.userId !== scope.userId || record.workspaceId !== scope.workspaceId
        || record.pageId !== scope.pageId || !validRequest(record.request) || typeof record.confirmed !== "boolean"
        || (record.confirmed && (!id(record.destinationId) || record.destinationId === scope.pageId || typeof record.archived !== "boolean"))
        || ["acknowledged", "rejected", "cancelled"].some(field => record[field] !== undefined && typeof record[field] !== "boolean")
        || (record.acknowledged && !record.confirmed) || (record.rejected && record.confirmed)
        || (record.cancelled && !record.rejected)) throw invalid();
    return record;
  }
  function validResult(result, scope) {
    if (result?.contract !== CONTRACT || result.source_page_id !== scope.pageId
        || result.workspace?.id !== scope.workspaceId || result.page?.workspace_id !== scope.workspaceId
        || !id(result.page?.id) || result.page.id === scope.pageId
        || !Array.isArray(result.page_ids) || !result.page_ids.includes(result.page.id)) {
      throw new Error("복제 응답의 페이지 정보가 일치하지 않습니다. 같은 요청의 결과를 다시 확인해 주세요.");
    }
    return result;
  }

  // The shared store and Web Lock have the same origin/storage partition.
  // No content or grants are stored; every receipt is reauthorized by the server.
  function create({storage, locks, legacyStorage, transport, changed = () => {}}) {
    const flights = new Map();
    function store() {
      const value = storage();
      if (!value) throw new Error("복제 요청을 보관할 수 없습니다. 브라우저 저장소를 확인해 주세요.");
      return value;
    }
    function readFrom(target, scope) {
      let text;
      try { text = target.getItem(keyFor(scope)); } catch (_) { throw invalid(); }
      if (text === null) return null;
      try {
        if (text.length > 8192) throw invalid();
        return validate(JSON.parse(text), scope);
      } catch (_) { throw invalid(); }
    }
    function read(scope) { return readFrom(store(), scope); }
    function legacy(scope) {
      return legacyStorage ? readFrom(legacyStorage(), scope) : null;
    }
    function inspect(scope) {
      const record = read(scope), previous = legacy(scope);
      if (record && previous && fields.some(field => record.request[field] !== previous.request[field])) {
        throw new Error("서로 다른 복제 요청이 보관되어 있습니다. 기존 기록을 유지하며 새 복제를 중단했습니다.");
      }
      return record || previous;
    }
    function save(scope, record) {
      validate(record, scope);
      const text = JSON.stringify(record), key = keyFor(scope);
      try {
        const target = store();
        target.setItem(key, text);
        if (target.getItem(key) !== text) throw invalid();
      } catch (_) { throw new Error("복제 요청을 보관하지 못했습니다. 결과 확인 전에는 새 복제를 시작하지 마세요."); }
    }
    function adoptLegacy(scope) {
      let record = inspect(scope);
      const previous = legacy(scope);
      if (!previous) return record;
      if (previous.confirmed) {
        if (record.confirmed && record.destinationId !== previous.destinationId) throw invalid();
        record = {...record, confirmed: true, destinationId: previous.destinationId, archived: previous.archived};
      }
      save(scope, record);
      // Only retire a tab-local record after its exact operation is durable in
      // shared storage. Failure leaves both copies available for recovery.
      const target = legacyStorage(), key = keyFor(scope);
      target.removeItem(key);
      if (target.getItem(key) !== null) throw invalid();
      return record;
    }
    function list(scope) {
      const prefix = scopePrefix(scope), entries = [], keys = new Set();
      for (const target of [store(), ...(legacyStorage ? [legacyStorage()] : [])]) {
        for (let i = 0; i < target.length; i += 1) {
          const key = target.key(i);
          if (key?.startsWith(prefix)) keys.add(key);
        }
      }
      for (const key of keys) {
        let pageId;
        try { pageId = decodeURIComponent(key.slice(prefix.length)); } catch (_) { throw invalid(); }
        const record = inspect({...scope, pageId});
        if (record && !record.acknowledged && !record.rejected) entries.push({...record, busy: flights.has(key)});
      }
      return entries;
    }
    function notify() { try { changed(); } catch (_) { /* A render error cannot alter a copy receipt. */ } }
    function observe(event) {
      if (event.key !== null && !event.key?.startsWith(PREFIX)) return;
      try { if (event.storageArea === store()) notify(); } catch (_) { notify(); }
    }
    function exclusive(scope, action) {
      const manager = locks?.();
      if (!manager?.request) throw new Error("이 브라우저에서 복제 요청을 안전하게 조정할 수 없습니다. 복제를 시작하지 않았습니다.");
      // Never steal a lock or wait indefinitely on a stalled page. A competing
      // tab can observe the shared record and explicitly check it again.
      return manager.request(keyFor(scope), {mode: "exclusive", ifAvailable: true}, lock => lock ? action() : {state: "busy"});
    }
    function ensureCurrent(hooks) {
      if (!hooks.current()) throw new Error("화면이 바뀌었습니다. 원래 워크스페이스에서 복제 결과를 확인해 주세요.");
    }
    function acceptCancellation(scope, record, status) {
      const result = status?.result;
      if (record.confirmed || status?.state !== "cancelled" || result?.contract !== "code_assist.notion.canonical_page_copy_cancelled.v1"
          || result.state !== "cancelled" || result.workspace_id !== scope.workspaceId || result.source_page_id !== scope.pageId
          || result.operation_id !== record.request.operation_id
          || !validRequest(result.copy_request) || fields.some(field => result.copy_request[field] !== record.request[field])) throw invalid();
      save(scope, {...record, confirmed: false, rejected: true, cancelled: true});
      return status;
    }
    function run(scope, hooks) {
      scope = {...scope};
      const key = keyFor(scope);
      if (flights.has(key)) return flights.get(key);
      const flight = Promise.resolve().then(() => {
        const observed = inspect(scope);
        return exclusive(scope, async () => {
          ensureCurrent(hooks);
          let record = adoptLegacy(scope);
          if (observed && !record) throw invalid();
          if (hooks.operationId && record?.request.operation_id !== hooks.operationId) {
            throw new Error("확인하던 복제 요청이 바뀌었습니다. 현재 안내에서 다시 선택해 주세요.");
          }
          const terminal = record?.acknowledged || record?.rejected;
          const observedTerminal = observed?.acknowledged || observed?.rejected;
          if (terminal && !hooks.operationId && !hooks.lookupOnly && observedTerminal
              && observed.request.operation_id === record.request.operation_id) record = null;
          // Terminal markers stop a delayed click, issued before acknowledgement
          // or rejection, from silently creating a second operation.
          if (record?.rejected) throw new Error(record.cancelled ? "이 복제 요청은 취소되었습니다. 새로 복제해 주세요." : "복제 요청이 거절되었습니다. 현재 페이지에서 새로 복제해 주세요.");
          const firstSubmission = !record;
          const base = `/api/notion/workspaces/${encodeURIComponent(scope.workspaceId)}/pages/${encodeURIComponent(scope.pageId)}/duplicate`;
          const confirmed = (result, archived) => {
            validResult(result, scope);
            record = {...record, confirmed: true, destinationId: result.page.id, archived};
            save(scope, record);
            return {state: "committed", result, current: {archived}};
          };
          if (record) {
            const status = await transport(`${base}-result`, {method: "POST", body: clone(record.request)});
            if (status?.state === "cancelled") return acceptCancellation(scope, record, status);
            if (status?.state === "committed" && typeof status.current?.archived === "boolean") {
              return confirmed(status.result, status.current.archived);
            }
            if (status?.state !== "unknown" || record.confirmed) throw new Error("복제 결과를 확인하지 못했습니다. 요청을 다시 만들지 않았습니다.");
            if (hooks.lookupOnly) return {state: "unknown"};
          } else if (hooks.lookupOnly) return {state: "absent"};
          ensureCurrent(hooks);
          if (!record) {
            const request = await hooks.prepare();
            ensureCurrent(hooks);
            if (!validRequest(request)) throw invalid();
            record = {version: 1, ...scope, request: clone(request), confirmed: false};
            // Persist the immutable operation before any mutation can reach the server.
            save(scope, record);
            notify();
          } else {
            await hooks.beforeRetry();
            ensureCurrent(hooks);
          }
          let intentRegistered = false;
          try {
            const intent = await transport(`${base}-intent`, {method: "POST", body: clone(record.request)});
            if (intent?.state === "cancelled") return acceptCancellation(scope, record, intent);
            if (intent?.state === "committed" && typeof intent.current?.archived === "boolean") {
              return confirmed(intent.result, intent.current.archived);
            }
            if (intent?.state !== "pending" || intent.source_page_id !== scope.pageId
                || !validRequest(intent.request)
                || fields.some(field => intent.request[field] !== record.request[field])) {
              throw new Error("복제 요청의 서버 기록을 확인하지 못했습니다. 복제를 시작하지 않았습니다.");
            }
            intentRegistered = true;
            ensureCurrent(hooks);
            const result = await transport(base, {method: "POST", body: clone(record.request)});
            return confirmed(result, false);
          } catch (error) {
            // Only a rejection before intent registration can release a fresh
            // request. Once the server has registered it, even a definitive copy
            // failure leaves a discoverable intent that needs explicit resolution.
            if (firstSubmission && !intentRegistered
                && [400, 401, 403, 404, 409, 422].includes(error?.status)) {
              save(scope, {...record, rejected: true});
            }
            throw error;
          }
        });
      }).finally(() => { flights.delete(key); notify(); });
      flights.set(key, flight);
      notify();
      return flight;
    }
    function cancel(scope, hooks) {
      scope = {...scope};
      const key = keyFor(scope);
      if (flights.has(key)) return Promise.resolve({state: "busy"});
      const flight = Promise.resolve().then(() => exclusive(scope, async () => {
        ensureCurrent(hooks);
        const record = adoptLegacy(scope);
        if (!record) return {state: "absent"};
        if (hooks.operationId !== record.request.operation_id) throw new Error("취소하려던 복제 요청이 바뀌었습니다. 현재 안내에서 다시 선택해 주세요.");
        const status = await transport(`/api/notion/workspaces/${encodeURIComponent(scope.workspaceId)}/pages/${encodeURIComponent(scope.pageId)}/duplicate-cancel`,
          {method: "POST", body: clone(record.request)});
        if (status?.state === "committed" && typeof status.current?.archived === "boolean") {
          validResult(status.result, scope);
          save(scope, {...record, confirmed: true, rejected: false, cancelled: false, destinationId: status.result.page.id, archived: status.current.archived});
          return status;
        }
        return acceptCancellation(scope, record, status);
      })).finally(() => { flights.delete(key); notify(); });
      flights.set(key, flight); notify(); return flight;
    }
    async function acknowledge(scope, operationId) {
      scope = {...scope};
      if (flights.has(keyFor(scope))) return false;
      const result = await exclusive(scope, () => {
        const record = adoptLegacy(scope);
        if (!record?.confirmed || record.request.operation_id !== operationId) return false;
        save(scope, {...record, acknowledged: true});
        return true;
      });
      notify(); return result === true;
    }
    async function adopt(scope, request) {
      scope = {...scope};
      if (!validRequest(request) || flights.has(keyFor(scope))) return false;
      const result = await exclusive(scope, () => {
        const existing = adoptLegacy(scope);
        if (existing && fields.every(field => existing.request[field] === request[field])) return true;
        if (existing && !existing.acknowledged && !existing.rejected) {
          throw new Error("다른 복제 요청이 이 브라우저에 남아 있습니다. 먼저 그 결과를 확인해 주세요.");
        }
        save(scope, {version: 1, ...scope, request: clone(request), confirmed: false});
        return true;
      });
      notify(); return result === true;
    }
    function pending(scope) {
      const prefix = scopePrefix(scope);
      return [...flights.keys()].some(key => key.startsWith(prefix)) || list(scope).some(item => !item.confirmed);
    }
    return {run, cancel, list, acknowledge, adopt, pending, observe};
  }
  return {create};
});

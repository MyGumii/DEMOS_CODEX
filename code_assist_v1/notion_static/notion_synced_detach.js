(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.NotionSyncedDetach = api;
})(typeof globalThis === 'object' ? globalThis : this, function () {
  'use strict';

  const PREFIX = 'notion.synced-detach.v1:';
  const CONTRACT = 'code_assist.notion.synced_detach_commit.v1';
  const witnessFields = ['document_id', 'workspace_id', 'workspace_epoch',
    'document_sequence', 'document_epoch', 'generation'];
  const clone = value => JSON.parse(JSON.stringify(value));
  const id = value => typeof value === 'string' && value.length > 0 && value.length <= 256;
  const invalid = () => new Error('저장된 동기화 해제 요청을 확인할 수 없습니다. 새 요청을 시작하지 않았습니다.');

  function prefix(scope) {
    if (!id(scope?.userId) || !id(scope?.workspaceId)) throw invalid();
    return PREFIX + [scope.userId, scope.workspaceId].map(encodeURIComponent).join(':') + ':';
  }
  function key(scope) {
    if (!id(scope?.pageId) || !id(scope?.blockId)) throw invalid();
    return prefix(scope) + [scope.pageId, scope.blockId].map(encodeURIComponent).join(':');
  }
  function validRequest(request, scope) {
    if (!request || typeof request !== 'object' || Array.isArray(request)
        || Object.keys(request).sort().join() !== 'operation_id,origin,witnesses'
        || !id(request.operation_id) || !request.origin || Array.isArray(request.origin)
        || Object.keys(request.origin).sort().join() !== 'block_id,document_id'
        || !id(request.origin.document_id) || !id(request.origin.block_id)
        || request.origin.document_id === scope.pageId && request.origin.block_id === scope.blockId
        || !Array.isArray(request.witnesses) || request.witnesses.length < 1
        || request.witnesses.length > 2) return false;
    const documents = new Set();
    for (const witness of request.witnesses) {
      if (!witness || Array.isArray(witness)
          || Object.keys(witness).sort().join() !== [...witnessFields].sort().join()
          || !id(witness.document_id) || !id(witness.workspace_id)
          || witness.document_id === scope.pageId && witness.workspace_id !== scope.workspaceId
          || !witnessFields.slice(2).every(field => Number.isSafeInteger(witness[field]) && witness[field] > 0)
          || documents.has(witness.document_id)) return false;
      documents.add(witness.document_id);
    }
    const required = new Set([scope.pageId, request.origin.document_id]);
    return documents.size === required.size && [...required].every(document => documents.has(document));
  }
  function validate(record, scope) {
    if (!record || Object.keys(record).sort().join() !==
        'acknowledged,blockId,confirmed,pageId,request,userId,version,workspaceId'
        || record.version !== 1 || record.userId !== scope.userId
        || record.workspaceId !== scope.workspaceId || record.pageId !== scope.pageId
        || record.blockId !== scope.blockId || !validRequest(record.request, scope)
        || typeof record.confirmed !== 'boolean' || typeof record.acknowledged !== 'boolean'
        || record.acknowledged && !record.confirmed) throw invalid();
    return record;
  }
  function validResult(result, scope, request) {
    if (result?.contract !== CONTRACT || result.workspace_id !== scope.workspaceId
        || result.document_id !== scope.pageId || result.block_id !== scope.blockId
        || result.operation_id !== request.operation_id || typeof result.replayed !== 'boolean'
        || result.origin?.document_id !== request.origin.document_id
        || result.origin?.block_id !== request.origin.block_id) {
      throw new Error('동기화 해제 결과의 페이지 또는 블록이 일치하지 않습니다. 같은 요청의 결과를 확인해 주세요.');
    }
    return result;
  }

  function create({storage, locks, transport, changed = () => {}}) {
    const flights = new Map();
    function target() {
      const value = storage?.();
      if (!value) throw new Error('동기화 해제 요청을 보관할 수 없습니다. 브라우저 저장소를 확인해 주세요.');
      return value;
    }
    function read(scope) {
      let raw;
      try { raw = target().getItem(key(scope)); } catch (_) { throw invalid(); }
      if (raw === null) return null;
      try {
        if (raw.length > 8192) throw invalid();
        return validate(JSON.parse(raw), scope);
      } catch (_) { throw invalid(); }
    }
    function save(scope, record) {
      validate(record, scope);
      const raw = JSON.stringify(record);
      if (raw.length > 8192) throw invalid();
      try {
        const value = target();
        value.setItem(key(scope), raw);
        if (value.getItem(key(scope)) !== raw) throw invalid();
      } catch (_) {
        throw new Error('동기화 해제 요청을 보관하지 못했습니다. 결과 확인 전에는 새 요청을 시작하지 마세요.');
      }
      try { changed(); } catch (_) { /* UI errors cannot change the receipt. */ }
    }
    function discardRejected(scope) {
      try {
        const value = target();
        value.removeItem(key(scope));
        if (value.getItem(key(scope)) !== null) throw invalid();
      } catch (_) {
        throw new Error('거부된 동기화 해제 요청을 정리하지 못했습니다. 결과 확인 전에는 새 요청을 시작하지 마세요.');
      }
      try { changed(); } catch (_) { /* UI errors cannot change the receipt. */ }
    }
    function list(scope) {
      const start = prefix(scope), value = target(), results = [];
      for (let i = 0; i < value.length; i += 1) {
        const item = value.key(i);
        if (!item?.startsWith(start)) continue;
        const suffix = item.slice(start.length).split(':');
        if (suffix.length !== 2) throw invalid();
        let pageId, blockId;
        try { [pageId, blockId] = suffix.map(decodeURIComponent); } catch (_) { throw invalid(); }
        const entry = read({...scope, pageId, blockId});
        if (entry && !entry.acknowledged) results.push({...entry, busy: flights.has(item)});
      }
      return results;
    }
    function observe(event) {
      if (event.key !== null && !event.key?.startsWith(PREFIX)) return;
      try { if (event.storageArea === target()) changed(); } catch (_) { changed(); }
    }
    function exclusive(scope, action) {
      const manager = locks?.();
      if (!manager?.request) throw new Error('이 브라우저에서 동기화 해제 요청을 안전하게 조정할 수 없습니다.');
      return manager.request(key(scope), {mode: 'exclusive', ifAvailable: true},
        lock => lock ? action() : {state: 'busy'});
    }
    function current(hooks) {
      if (!hooks.current()) throw new Error('화면이 바뀌었습니다. 원래 워크스페이스에서 결과를 확인해 주세요.');
    }
    function run(scope, hooks) {
      scope = {...scope};
      const identity = key(scope);
      if (flights.has(identity)) return flights.get(identity);
      const flight = Promise.resolve().then(() => exclusive(scope, async () => {
        current(hooks);
        let record = read(scope);
        if (hooks.operationId && record?.request.operation_id !== hooks.operationId) throw invalid();
        const base = `/api/notion/workspaces/${encodeURIComponent(scope.workspaceId)}/pages/${encodeURIComponent(scope.pageId)}/synced-blocks/${encodeURIComponent(scope.blockId)}`;
        const committed = (result, status) => {
          validResult(result, scope, record.request);
          hooks.validateResult?.(result);
          save(scope, {...record, confirmed: true});
          return {state: 'committed', result, current: status || {archived: false}};
        };
        const acceptReceipt = status => {
          if (status?.state !== 'committed') return null;
          if (typeof status.current?.archived !== 'boolean'
              || !Number.isSafeInteger(status.current.document_sequence)
              || status.current.document_sequence < 1) throw invalid();
          return committed(status.result, status.current);
        };
        if (record) {
          const status = await transport(base + '/detach-result', {method: 'POST', body: clone(record.request)});
          const saved = acceptReceipt(status);
          if (saved) return saved;
          if (status?.state !== 'unknown' || record.confirmed) throw invalid();
          if (hooks.lookupOnly) return {state: 'unknown'};
          await hooks.beforeRetry();
        } else {
          if (hooks.lookupOnly) return {state: 'absent'};
          const request = await hooks.prepare();
          current(hooks);
          if (!validRequest(request, scope)) throw invalid();
          record = {version: 1, ...scope, request: clone(request), confirmed: false, acknowledged: false};
          // Persist the immutable operation before the first server mutation.
          save(scope, record);
        }
        current(hooks);
        try {
          const result = await transport(base + '/detach', {method: 'POST', body: clone(record.request)});
          return committed(result);
        } catch (error) {
          if (error?.status !== 409 || error?.data?.code !== 'synced_source_changed') throw error;
          // A stale witness cannot commit. Check the exact receipt once more in
          // case another request using this operation already finished.
          const status = await transport(base + '/detach-result', {method: 'POST', body: clone(record.request)});
          const saved = acceptReceipt(status);
          if (saved) return saved;
          if (status?.state !== 'unknown') throw invalid();
          discardRejected(scope);
          return {state: 'stale'};
        }
      })).finally(() => {
        flights.delete(identity);
        try { changed(); } catch (_) { /* No mutation depends on a repaint. */ }
      });
      flights.set(identity, flight);
      try { changed(); } catch (_) { /* No mutation depends on a repaint. */ }
      return flight;
    }
    async function acknowledge(scope, operationId) {
      scope = {...scope};
      if (flights.has(key(scope))) return false;
      const result = await exclusive(scope, () => {
        const record = read(scope);
        if (!record?.confirmed || record.request.operation_id !== operationId) return false;
        save(scope, {...record, acknowledged: true});
        return true;
      });
      return result === true;
    }
    function pending(scope) {
      const start = prefix(scope);
      return [...flights.keys()].some(item => item.startsWith(start))
        || list(scope).some(item => !item.confirmed);
    }
    return {run, list, acknowledge, pending, observe};
  }
  return {create, validRequest, validResult};
});

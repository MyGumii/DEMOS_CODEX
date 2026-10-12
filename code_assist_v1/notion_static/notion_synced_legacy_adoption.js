(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.NotionSyncedLegacyAdoption = api;
})(typeof globalThis === 'object' ? globalThis : this, function () {
  'use strict';
  const PREFIX = 'notion.synced-legacy-adoption.v1:';
  const CONTRACT = 'code_assist.notion.synced_legacy_adoption_commit.v1';
  const CANCEL = 'code_assist.notion.synced_legacy_adoption_cancel.v1';
  const clone = value => JSON.parse(JSON.stringify(value));
  const id = value => typeof value === 'string' && value.length > 0 && value.length <= 256;
  const invalid = () => Error('전환 요청의 범위를 확인할 수 없습니다. 원래 워크스페이스에서 결과를 확인해 주세요.');
  const endpoint = value => value && Object.keys(value).sort().join() === 'block_id,document_id'
    && id(value.document_id) && id(value.block_id);
  const identity = value => JSON.stringify([value.document_id, value.block_id]);
  const compare = (a, b) => a.document_id === b.document_id
    ? (a.block_id > b.block_id ? 1 : a.block_id < b.block_id ? -1 : 0)
    : (a.document_id > b.document_id ? 1 : -1);
  function validRequest(value) {
    if (!value || Object.keys(value).sort().join() !== 'operation_id,origin,preparation_sha256,references'
        || !id(value.operation_id) || !endpoint(value.origin)
        || !/^sha256:[0-9a-f]{64}$/.test(value.preparation_sha256)
        || !Array.isArray(value.references) || value.references.length > 127
        || !value.references.every(endpoint)) return false;
    const keys = [value.origin, ...value.references].map(identity);
    return new Set(keys).size === keys.length;
  }
  function normalize(value) {
    if (!validRequest(value)) throw invalid();
    const result = clone(value);
    result.references.sort(compare);
    return result;
  }
  const equal = (a, b) => identity(a.origin) === identity(b.origin)
    && JSON.stringify(a.references) === JSON.stringify(b.references)
    && a.operation_id === b.operation_id && a.preparation_sha256 === b.preparation_sha256;
  function prefix(scope) {
    if (!id(scope?.userId) || !id(scope?.workspaceId)) throw invalid();
    return PREFIX + [scope.userId, scope.workspaceId].map(encodeURIComponent).join(':') + ':';
  }
  function key(scope, request) { return prefix(scope) + encodeURIComponent(request.operation_id); }
  function validStatus(status, scope, request) {
    if (status?.state === 'unknown') return status;
    const result = status?.result;
    if (!['committed', 'cancelled'].includes(status?.state)
        || result?.contract !== (status.state === 'committed' ? CONTRACT : CANCEL)
        || result.workspace_id !== scope.workspaceId || typeof result.replayed !== 'boolean'
        || !equal(normalize({origin: result.origin, references: result.references,
          operation_id: result.operation_id, preparation_sha256: result.preparation_sha256}), request)) throw invalid();
    if (status.state === 'committed') {
      const documents = [...new Set([request.origin, ...request.references].map(item => item.document_id))].sort();
      if (!Array.isArray(status.current) || JSON.stringify(status.current.map(item => item.document_id).sort()) !== JSON.stringify(documents)
          || !status.current.every(item => typeof item.archived === 'boolean'
            && Number.isSafeInteger(item.document_sequence) && item.document_sequence > 0
            && Number.isSafeInteger(item.document_epoch) && item.document_epoch > 0)) throw invalid();
    }
    return status;
  }
  function create({storage, locks, transport, changed = () => {}}) {
    const flights = new Map();
    const notify = () => { try { changed(); } catch (_) { /* Receipt survives rendering failures. */ } };
    function target() {
      const value = storage?.();
      if (!value) throw Error('요청을 보관할 수 없습니다. 브라우저 저장소를 확인해 주세요.');
      return value;
    }
    function validate(record, scope) {
      if (!record || Object.keys(record).sort().join() !== 'request,state,userId,version,workspaceId'
          || record.version !== 1 || record.userId !== scope.userId || record.workspaceId !== scope.workspaceId
          || !['pending', 'committed', 'cancelled'].includes(record.state) || !validRequest(record.request)) throw invalid();
      return record;
    }
    function read(scope, request) {
      const raw = target().getItem(key(scope, request));
      if (raw === null) return null;
      try {
        if (raw.length > 262144) throw invalid();
        const record = validate(JSON.parse(raw), scope);
        if (!equal(normalize(record.request), request)) throw invalid();
        return record;
      } catch (_) { throw invalid(); }
    }
    function save(scope, record) {
      validate(record, scope);
      const raw = JSON.stringify(record), value = target();
      if (raw.length > 262144) throw invalid();
      value.setItem(key(scope, record.request), raw);
      if (value.getItem(key(scope, record.request)) !== raw) throw Error('전환 요청을 보관하지 못했습니다. 새 요청을 시작하지 마세요.');
      notify();
    }
    function list(scope) {
      const start = prefix(scope), value = target(), result = [];
      for (let i = 0; i < value.length; i++) {
        const item = value.key(i);
        if (!item?.startsWith(start)) continue;
        const raw = value.getItem(item);
        try {
          if (raw.length > 262144) throw invalid();
          const record = validate(JSON.parse(raw), scope);
          if (item !== key(scope, record.request)) throw invalid();
          result.push({...record, busy: flights.has(item)});
        } catch (_) { throw invalid(); }
      }
      return result;
    }
    function run(scope, value, {action = 'adopt', current, beforeWrite = async () => {}}) {
      scope = {...scope};
      const request = normalize(value), operation = key(scope, request);
      if (!['adopt', 'result', 'cancel'].includes(action)) throw invalid();
      if (flights.has(operation)) return flights.get(operation);
      const check = () => { if (!current()) throw invalid(); };
      const flight = Promise.resolve().then(() => {
        const manager = locks?.();
        if (!manager?.request) throw Error('이 브라우저에서 요청을 안전하게 조정할 수 없습니다.');
        return manager.request(operation, {mode: 'exclusive', ifAvailable: true}, async lock => {
          if (!lock) return {state: 'busy'};
          check();
          let record = read(scope, request);
          if (!record) {
            record = {version: 1, userId: scope.userId, workspaceId: scope.workspaceId, request, state: 'pending'};
            save(scope, record); // Durable before any mutation, including intent registration.
          }
          const base = `/api/notion/workspaces/${encodeURIComponent(scope.workspaceId)}/synced-legacy/`;
          const post = name => transport(base + name, {method: 'POST', body: clone(request)});
          const accept = data => {
            const status = validStatus(data, scope, request);
            if (status.state === 'unknown') {
              if (record.state !== 'pending') throw invalid();
              return null;
            }
            save(scope, {...record, state: status.state});
            return status;
          };
          const saved = accept(await post('result'));
          if (saved) return saved;
          if (action === 'result') return {state: 'unknown'};
          check();
          if (action === 'cancel') return accept(await post('cancel'));
          await beforeWrite();
          check();
          const intent = await post('intent');
          if (intent?.state !== 'pending') return accept(intent);
          if (!equal(normalize(intent.request), request)) throw invalid();
          check();
          // A lost reply keeps the immutable pending record and server intent.
          return accept(await post('adopt'));
        });
      }).finally(() => { flights.delete(operation); notify(); });
      flights.set(operation, flight); notify();
      return flight;
    }
    function observe(event) {
      if (event.key === null || event.key?.startsWith(PREFIX)) notify();
    }
    return {run, list, observe, pending: scope => list(scope).some(item => item.state === 'pending' || item.busy)};
  }
  return {create, validRequest, validStatus};
});

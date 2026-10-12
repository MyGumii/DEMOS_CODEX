(function(root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.NotionDatabaseMove = api;
})(typeof globalThis === 'object' ? globalThis : this, function() {
  'use strict';

  const PREFIX = 'notion.database-move.v1:';
  const CONTRACT = 'code_assist.notion.owned_database_move.v1';
  const FIELDS = ['source_document_id', 'destination_document_id', 'operation_id',
    'expected_source_sequence', 'expected_destination_sequence',
    'expected_workspace_epoch', 'expected_source_epoch', 'expected_destination_epoch'];
  const id = value => typeof value === 'string' && value.length > 0 && value.length <= 256;
  const clone = value => JSON.parse(JSON.stringify(value));
  const invalid = () => new Error('저장된 데이터베이스 이동 요청을 확인할 수 없습니다. 새 이동을 시작하지 않았습니다.');

  function prefix(scope) {
    if (!id(scope?.userId) || !id(scope?.workspaceId)) throw invalid();
    return PREFIX + [scope.userId, scope.workspaceId].map(encodeURIComponent).join(':') + ':';
  }
  function key(scope) {
    if (!id(scope?.sourceDocumentId) || !id(scope?.databaseId)) throw invalid();
    return prefix(scope) + [scope.sourceDocumentId, scope.databaseId].map(encodeURIComponent).join(':');
  }
  function requestValid(request, scope) {
    return request && typeof request === 'object' && !Array.isArray(request)
      && Object.keys(request).length === FIELDS.length
      && FIELDS.every(field => Object.hasOwn(request, field))
      && request.source_document_id === scope.sourceDocumentId
      && id(request.destination_document_id)
      && request.destination_document_id !== scope.sourceDocumentId
      && id(request.operation_id)
      && FIELDS.slice(3).every(field => Number.isSafeInteger(request[field]) && request[field] > 0);
  }
  function validate(record, scope) {
    if (!record || record.version !== 1
      || !['userId', 'workspaceId', 'sourceDocumentId', 'databaseId']
        .every(field => record[field] === scope[field])
      || !requestValid(record.request, scope)
      || typeof record.confirmed !== 'boolean'
      || typeof record.acknowledged !== 'boolean'
      || typeof record.rejected !== 'boolean'
      || (record.confirmed && typeof record.destinationArchived !== 'boolean')
      || (record.acknowledged && !record.confirmed)
      || (record.rejected && record.confirmed)) throw invalid();
    return record;
  }
  function resultValid(result, scope, request) {
    if (result?.contract !== CONTRACT || result.workspace_id !== scope.workspaceId
      || result.source_document_id !== scope.sourceDocumentId
      || result.destination_document_id !== request.destination_document_id
      || result.database_id !== scope.databaseId || !id(result.block_id)
      || result.operation_id !== request.operation_id) {
      throw new Error('데이터베이스 이동 응답이 요청과 일치하지 않습니다. 같은 요청의 결과를 확인해 주세요.');
    }
    return result;
  }

  function create({storage, locks, transport, changed = () => {}}) {
    const flights = new Map();
    function store() {
      const value = storage();
      if (!value) throw new Error('데이터베이스 이동 요청을 보관할 수 없습니다.');
      return value;
    }
    function read(scope) {
      let raw;
      try { raw = store().getItem(key(scope)); } catch (_) { throw invalid(); }
      if (raw === null) return null;
      try {
        if (raw.length > 8192) throw invalid();
        return validate(JSON.parse(raw), scope);
      } catch (_) { throw invalid(); }
    }
    function save(scope, record) {
      validate(record, scope);
      const raw = JSON.stringify(record), target = store();
      try {
        target.setItem(key(scope), raw);
        if (target.getItem(key(scope)) !== raw) throw invalid();
      } catch (_) {
        throw new Error('이동 요청을 보관하지 못했습니다. 결과 확인 전에는 새 이동을 시작하지 마세요.');
      }
    }
    function notify() { try { changed(); } catch (_) { /* Receipt survives UI errors. */ } }
    function list(scope) {
      const result = [], start = prefix(scope), target = store();
      for (let index = 0; index < target.length; index += 1) {
        const entry = target.key(index);
        if (!entry?.startsWith(start)) continue;
        const parts = entry.slice(start.length).split(':');
        if (parts.length !== 2) throw invalid();
        let sourceDocumentId, databaseId;
        try { [sourceDocumentId, databaseId] = parts.map(decodeURIComponent); }
        catch (_) { throw invalid(); }
        const record = read({...scope, sourceDocumentId, databaseId});
        if (record && !record.acknowledged && !record.rejected)
          result.push({...record, busy:flights.has(entry)});
      }
      return result;
    }
    function observe(event) {
      if (event.key !== null && !event.key?.startsWith(PREFIX)) return;
      try { if (event.storageArea === store()) notify(); } catch (_) { notify(); }
    }
    function exclusive(scope, action) {
      const manager = locks?.();
      if (!manager?.request) throw new Error('이 브라우저에서 이동 요청을 안전하게 조정할 수 없습니다.');
      return manager.request(key(scope), {mode:'exclusive', ifAvailable:true},
        lock => lock ? action() : {state:'busy'});
    }
    function current(hooks) {
      if (!hooks.current()) throw new Error('화면이 바뀌었습니다. 원래 워크스페이스에서 이동 결과를 확인해 주세요.');
    }
    function run(scope, hooks) {
      scope = {...scope};
      const identity = key(scope);
      if (flights.has(identity)) return flights.get(identity);
      const flight = Promise.resolve().then(() => exclusive(scope, async () => {
        current(hooks);
        let record = read(scope);
        if (hooks.operationId && record?.request.operation_id !== hooks.operationId)
          throw new Error('확인하던 이동 요청이 바뀌었습니다.');
        if (!hooks.operationId && !hooks.lookupOnly && (record?.acknowledged || record?.rejected)) record = null;
        if (record && hooks.destinationId
            && record.request.destination_document_id !== hooks.destinationId)
          throw new Error('확인되지 않은 다른 이동 요청이 있습니다. 먼저 그 결과를 확인해 주세요.');
        const firstSubmission = !record;
        const base = `/api/notion/workspaces/${encodeURIComponent(scope.workspaceId)}`
          + `/databases/${encodeURIComponent(scope.databaseId)}/move`;
        const confirm = (result, destinationArchived) => {
          resultValid(result, scope, record.request);
          record = {...record, confirmed:true, acknowledged:false, rejected:false,
            destinationArchived};
          save(scope, record);
          return {state:'committed', result, current:{destination_archived:destinationArchived}};
        };
        if (record) {
          const status = await transport(base + '-result', {method:'POST', body:clone(record.request)});
          if (status?.state === 'committed'
              && typeof status.current?.destination_archived === 'boolean')
            return confirm(status.result, status.current.destination_archived);
          if (status?.state !== 'unknown' || record.confirmed) throw invalid();
          if (hooks.lookupOnly) return {state:'unknown'};
        } else if (hooks.lookupOnly) return {state:'absent'};
        current(hooks);
        if (!record) {
          const request = await hooks.prepare();
          current(hooks);
          if (!requestValid(request, scope)
              || request.destination_document_id !== hooks.destinationId) throw invalid();
          record = {version:1, ...scope, request:clone(request), confirmed:false,
            acknowledged:false, rejected:false};
          save(scope, record); notify();
        } else {
          await hooks.beforeRetry(); current(hooks);
        }
        try {
          return confirm(await transport(base, {method:'POST', body:clone(record.request)}), false);
        } catch (error) {
          if (firstSubmission && [400, 401, 403, 404, 409, 422].includes(error?.status))
            save(scope, {...record, rejected:true});
          throw error;
        }
      })).finally(() => { flights.delete(identity); notify(); });
      flights.set(identity, flight); notify();
      return flight;
    }
    async function acknowledge(scope, operationId) {
      if (flights.has(key(scope))) return false;
      const result = await exclusive(scope, () => {
        const record = read(scope);
        if (!record?.confirmed || record.request.operation_id !== operationId) return false;
        save(scope, {...record, acknowledged:true}); return true;
      });
      notify(); return result === true;
    }
    function pending(scope) {
      return [...flights.keys()].some(entry => entry.startsWith(prefix(scope)))
        || list(scope).some(entry => !entry.confirmed);
    }
    return {run, list, acknowledge, pending, observe};
  }
  return {create};
});

(function(root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.NotionDatabaseTrash = api;
})(typeof globalThis === 'object' ? globalThis : this, function() {
  'use strict';

  const PREFIX = 'notion.database-trash.v1:';
  const ROW_PREFIX = 'notion.database-row-trash.v1:';
  const BATCH_PREFIX = 'notion.database-row-trash-batch.v1:';
  const CONTRACT = 'code_assist.notion.owned_database_trash.v1';
  const ROW_CONTRACT = 'code_assist.notion.database_row_trash.v1';
  const BATCH_CONTRACT = 'code_assist.notion.database_row_trash_batch.v1';
  const FIELDS = ['document_id', 'archived', 'operation_id',
    'expected_document_sequence', 'expected_workspace_epoch',
    'expected_document_epoch', 'expected_database_version'];
  const ROW_FIELDS = [...FIELDS, 'expected_row_version'];
  const BATCH_FIELDS = [...FIELDS, 'rows'];
  const id = value => typeof value === 'string' && value.length > 0 && value.length <= 256;
  const clone = value => JSON.parse(JSON.stringify(value));
  const invalid = () => new Error('저장된 휴지통 요청을 확인할 수 없습니다. 새 요청을 시작하지 않았습니다.');

  function prefix(scope, rowMode = false, batchMode = false) {
    if (!id(scope?.userId) || !id(scope?.workspaceId)) throw invalid();
    return (batchMode ? BATCH_PREFIX : rowMode ? ROW_PREFIX : PREFIX)
      + [scope.userId, scope.workspaceId].map(encodeURIComponent).join(':') + ':';
  }
  function key(scope, rowMode = false, batchMode = false) {
    if (!id(scope?.documentId) || !id(scope?.databaseId)) throw invalid();
    if (rowMode && !id(scope?.rowId)) throw invalid();
    return prefix(scope, rowMode, batchMode) + [scope.documentId, scope.databaseId,
      ...(rowMode ? [scope.rowId] : [])].map(encodeURIComponent).join(':');
  }
  function validRequest(request, scope, rowMode = false, batchMode = false) {
    const fields = batchMode ? BATCH_FIELDS : rowMode ? ROW_FIELDS : FIELDS;
    return request && typeof request === 'object' && !Array.isArray(request)
      && Object.keys(request).length === fields.length
      && fields.every(field => Object.hasOwn(request, field))
      && request.document_id === scope.documentId && typeof request.archived === 'boolean'
      && id(request.operation_id)
      && FIELDS.slice(3).every(field => Number.isSafeInteger(request[field]) && request[field] > 0)
      && (!rowMode || (Number.isSafeInteger(request.expected_row_version)
        && request.expected_row_version > 0))
      && (!batchMode || (Array.isArray(request.rows)
        && request.rows.length >= 1 && request.rows.length <= 100
        && request.rows.every(item => item && typeof item === 'object'
          && !Array.isArray(item) && Object.keys(item).length === 2
          && id(item.row_id) && Number.isSafeInteger(item.expected_row_version)
          && item.expected_row_version > 0)
        && new Set(request.rows.map(item => item.row_id)).size === request.rows.length));
  }
  function validate(record, scope, rowMode = false, batchMode = false) {
    if (!record || record.version !== 1
      || !['userId', 'workspaceId', 'documentId', 'databaseId',
        ...(rowMode ? ['rowId'] : [])]
        .every(field => record[field] === scope[field])
      || !validRequest(record.request, scope, rowMode, batchMode)
      || (record.label !== undefined
        && (typeof record.label !== 'string' || record.label.length > 200))
      || typeof record.confirmed !== 'boolean'
      || typeof record.acknowledged !== 'boolean'
      || typeof record.rejected !== 'boolean'
      || (record.acknowledged && !record.confirmed)
      || (record.rejected && record.confirmed)) throw invalid();
    return record;
  }
  function validResult(result, scope, request, rowMode = false, batchMode = false) {
    if (result?.contract !== (batchMode ? BATCH_CONTRACT : rowMode ? ROW_CONTRACT : CONTRACT)
      || result.workspace_id !== scope.workspaceId
      || result.document_id !== scope.documentId || result.database_id !== scope.databaseId
      || result.archived !== request.archived || result.operation_id !== request.operation_id
      || (batchMode
        ? !Array.isArray(result.rows) || result.rows.length !== request.rows.length
          || result.rows.some((item, index) => item?.row_id !== request.rows[index].row_id
            || item.row_version !== request.rows[index].expected_row_version + 1)
        : rowMode ? result.row_id !== scope.rowId : !id(result.block_id)))
      throw new Error('데이터베이스 휴지통 응답이 요청과 일치하지 않습니다. 결과를 다시 확인해 주세요.');
    return result;
  }
  function create({storage, locks, transport, changed = () => {}, rowMode = false,
      batchMode = false}) {
    if (rowMode && batchMode) throw invalid();
    const flights = new Map();
    const scopedKey = scope => key(scope, rowMode, batchMode);
    const scopedPrefix = scope => prefix(scope, rowMode, batchMode);
    function store() {
      const value = storage();
      if (!value) throw new Error('데이터베이스 휴지통 요청을 보관할 수 없습니다.');
      return value;
    }
    function read(scope) {
      let raw;
      try { raw = store().getItem(scopedKey(scope)); } catch (_) { throw invalid(); }
      if (raw === null) return null;
      try {
        if (raw.length > (batchMode ? 65536 : 8192)) throw invalid();
        return validate(JSON.parse(raw), scope, rowMode, batchMode);
      } catch (_) { throw invalid(); }
    }
    function save(scope, record) {
      validate(record, scope, rowMode, batchMode);
      const raw = JSON.stringify(record), target = store();
      try {
        target.setItem(scopedKey(scope), raw);
        if (target.getItem(scopedKey(scope)) !== raw) throw invalid();
      } catch (_) { throw new Error('휴지통 요청을 보관하지 못했습니다. 결과 확인 전에는 새 요청을 시작하지 마세요.'); }
    }
    function notify() { try { changed(); } catch (_) { /* The receipt remains durable. */ } }
    function list(scope) {
      const found = [], start = scopedPrefix(scope), target = store();
      for (let index = 0; index < target.length; index += 1) {
        const entry = target.key(index);
        if (!entry?.startsWith(start)) continue;
        const parts = entry.slice(start.length).split(':');
        if (parts.length !== (rowMode ? 3 : 2)) throw invalid();
        let documentId, databaseId, rowId;
        try { [documentId, databaseId, rowId] = parts.map(decodeURIComponent); }
        catch (_) { throw invalid(); }
        const record = read({...scope, documentId, databaseId,
          ...(rowMode ? {rowId} : {})});
        if (record && !record.acknowledged && !record.rejected)
          found.push({...record, busy:flights.has(entry)});
      }
      return found;
    }
    function observe(event) {
      if (event.key !== null && !event.key?.startsWith(batchMode ? BATCH_PREFIX
        : rowMode ? ROW_PREFIX : PREFIX)) return;
      try { if (event.storageArea === store()) notify(); } catch (_) { notify(); }
    }
    function exclusive(scope, action) {
      const manager = locks?.();
      if (!manager?.request) throw new Error('이 브라우저에서 휴지통 요청을 안전하게 조정할 수 없습니다.');
      return manager.request(scopedKey(scope), {mode:'exclusive', ifAvailable:true},
        lock => lock ? action() : {state:'busy'});
    }
    function current(hooks) {
      if (!hooks.current()) throw new Error('화면이 바뀌었습니다. 원래 워크스페이스에서 결과를 확인해 주세요.');
    }
    function run(scope, hooks) {
      scope = {...scope};
      const identity = scopedKey(scope);
      if (flights.has(identity)) return flights.get(identity);
      const flight = Promise.resolve().then(() => exclusive(scope, async () => {
        current(hooks);
        let record = read(scope);
        if (hooks.operationId && record?.request.operation_id !== hooks.operationId)
          throw new Error('확인하던 휴지통 요청이 바뀌었습니다.');
        if (!hooks.operationId && !hooks.lookupOnly
            && (record?.acknowledged || record?.rejected
              || (batchMode && record?.confirmed && hooks.replaceConfirmed))) record = null;
        if (batchMode && record && hooks.expectedRowIds
            && (record.request.rows.length !== hooks.expectedRowIds.length
              || record.request.rows.some((item, index) =>
                item.row_id !== hooks.expectedRowIds[index])))
          throw new Error('이전 여러 항목 휴지통 요청의 결과를 먼저 확인해 주세요.');
        if (record && record.request.archived !== hooks.archived) {
          if (!record.confirmed) throw new Error('확인되지 않은 반대 작업이 있습니다. 먼저 그 결과를 확인해 주세요.');
          record = null;
        }
        const firstSubmission = !record;
        const base = `/api/notion/workspaces/${encodeURIComponent(scope.workspaceId)}`
          + `/databases/${encodeURIComponent(scope.databaseId)}`
          + (batchMode ? (record?.request.archived ?? hooks.archived)
              ? '/rows/trash-batch' : '/rows/restore-batch'
            : rowMode ? `/rows/${encodeURIComponent(scope.rowId)}` : '');
        const confirm = result => {
          validResult(result, scope, record.request, rowMode, batchMode);
          record = {...record, confirmed:true, acknowledged:false, rejected:false};
          save(scope, record);
          return {state:'committed', result, current:{archived:result.archived}};
        };
        if (record) {
          const status = await transport(base + '/lifecycle-result',
            {method:'POST', body:clone(record.request)});
          if (status?.state === 'committed') return confirm(status.result);
          if (status?.state !== 'unknown' || record.confirmed) throw invalid();
          if (hooks.lookupOnly) return {state:'unknown'};
        } else if (hooks.lookupOnly) return {state:'absent'};
        current(hooks);
        if (!record) {
          const request = await hooks.prepare();
          current(hooks);
          if (!validRequest(request, scope, rowMode, batchMode)
              || request.archived !== hooks.archived) throw invalid();
          record = {version:1, ...scope, request:clone(request), confirmed:false,
            acknowledged:false, rejected:false,
            ...(rowMode || batchMode ? {label:String(hooks.label || '').trim().slice(0, 200)} : {})};
          save(scope, record); notify();
        } else {
          await hooks.beforeRetry(); current(hooks);
        }
        try {
          const body = clone(record.request);
          delete body.archived;
          return confirm(await transport(batchMode ? base
            : base + (record.request.archived ? '/trash' : '/restore'),
            {method:'POST', body}));
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
      if (flights.has(scopedKey(scope))) return false;
      const result = await exclusive(scope, () => {
        const record = read(scope);
        if (!record?.confirmed || record.request.operation_id !== operationId) return false;
        save(scope, {...record, acknowledged:true}); return true;
      });
      notify(); return result === true;
    }
    return {run, list, acknowledge, observe};
  }
  return {create, createRow: options => create({...options, rowMode:true}),
    createBatch: options => create({...options, batchMode:true})};
});

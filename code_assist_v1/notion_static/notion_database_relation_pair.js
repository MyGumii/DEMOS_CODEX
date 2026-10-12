(function(root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.NotionDatabaseRelationPair = api;
})(typeof globalThis === 'object' ? globalThis : this, function() {
  'use strict';

  const PREFIX = 'notion.database-relation-pair.v1:';
  const CONTRACT = 'code_assist.notion.database_relation_pair.v1';
  const id = value => typeof value === 'string' && value.length > 0 && value.length <= 256;
  const invalid = () => new Error('저장된 양방향 관계 요청을 확인할 수 없습니다.');
  const clone = value => JSON.parse(JSON.stringify(value));

  function prefix(scope) {
    if (!id(scope?.userId) || !id(scope?.workspaceId)) throw invalid();
    return PREFIX + [scope.userId, scope.workspaceId].map(encodeURIComponent).join(':') + ':';
  }
  function key(scope) {
    if (!id(scope?.pageId) || !id(scope?.propertyId)) throw invalid();
    return prefix(scope) + [scope.pageId, scope.propertyId].map(encodeURIComponent).join(':');
  }
  function valid(record, scope) {
    const body = record?.body;
    if (record?.version !== 1 || !['userId', 'workspaceId', 'pageId', 'propertyId']
      .every(field => record[field] === scope[field]) || !id(body?.operation_id)
      || !id(body?.database_id) || body?.target_id !== scope.propertyId
      || !['database.relation_pair.enable', 'database.relation_pair.disable']
        .includes(body?.command_kind)
      || !body.payload || typeof body.payload !== 'object'
      || !['expected_document_sequence', 'expected_workspace_epoch',
        'expected_document_epoch', 'expected_target_version'].every(field =>
        Number.isSafeInteger(body[field]) && body[field] > 0)) throw invalid();
    return record;
  }
  function resultValid(result, scope, body) {
    if (result?.contract !== CONTRACT || result.workspace_id !== scope.workspaceId
      || result.document_id !== scope.pageId || result.database_id !== body.database_id
      || result.property_id !== scope.propertyId
      || result.command_kind !== body.command_kind) {
      throw new Error('양방향 관계 응답이 보관된 요청과 일치하지 않습니다.');
    }
    return result;
  }

  function create({storage, locks, transport, changed = () => {}}) {
    const flights = new Map();
    const store = () => {
      const target = storage();
      if (!target) throw new Error('양방향 관계 요청을 보관할 수 없습니다.');
      return target;
    };
    function read(scope) {
      let raw;
      try { raw = store().getItem(key(scope)); } catch (_) { throw invalid(); }
      if (raw === null) return null;
      try {
        if (raw.length > 8192) throw invalid();
        return valid(JSON.parse(raw), scope);
      } catch (_) { throw invalid(); }
    }
    function save(scope, body) {
      const record = valid({version:1, ...scope, body:clone(body)}, scope);
      const raw = JSON.stringify(record), target = store();
      try {
        target.setItem(key(scope), raw);
        if (target.getItem(key(scope)) !== raw) throw invalid();
      } catch (_) { throw new Error('양방향 관계 요청을 보관하지 못해 전송하지 않았습니다.'); }
      return record;
    }
    function notify() { try { changed(); } catch (_) { /* The journal survives UI errors. */ } }
    function list(scope) {
      const target = store(), start = prefix(scope), entries = [];
      for (let index = 0; index < target.length; index += 1) {
        const item = target.key(index);
        if (!item?.startsWith(start)) continue;
        const parts = item.slice(start.length).split(':');
        if (parts.length !== 2) throw invalid();
        let pageId, propertyId;
        try { [pageId, propertyId] = parts.map(decodeURIComponent); }
        catch (_) { throw invalid(); }
        const record = read({...scope, pageId, propertyId});
        if (record) entries.push({...record, busy:flights.has(item)});
      }
      return entries;
    }
    function run(scope, {body = null, current = () => true} = {}) {
      scope = {...scope};
      const identity = key(scope);
      if (flights.has(identity)) return flights.get(identity);
      const flight = Promise.resolve().then(async () => {
        const manager = locks?.();
        if (!manager?.request) throw new Error('이 브라우저에서 관계 요청을 안전하게 조정할 수 없습니다.');
        return manager.request(identity, {mode:'exclusive', ifAvailable:true}, async lock => {
          if (!lock) return {state:'busy'};
          if (!current()) throw new Error('화면이 바뀌었습니다. 원래 워크스페이스에서 결과를 확인해 주세요.');
          let record = read(scope);
          if (record && body && JSON.stringify(record.body) !== JSON.stringify(body))
            throw new Error('확인되지 않은 다른 관계 요청이 있습니다. 먼저 결과를 확인해 주세요.');
          if (!record) {
            if (!body) return {state:'absent'};
            record = save(scope, body);
            notify();
          }
          const url = `/api/notion/workspaces/${encodeURIComponent(scope.workspaceId)}`
            + `/pages/${encodeURIComponent(scope.pageId)}/databases/commands`;
          const result = resultValid(await transport(url,
            {method:'POST', body:clone(record.body)}), scope, record.body);
          if (!current()) return {state:'committed', result};
          store().removeItem(identity);
          notify();
          return {state:'committed', result};
        });
      }).finally(() => { flights.delete(identity); notify(); });
      flights.set(identity, flight);
      notify();
      return flight;
    }
    function observe(event) {
      if (event.key !== null && !event.key?.startsWith(PREFIX)) return;
      try { if (event.storageArea === store()) notify(); } catch (_) { notify(); }
    }
    return {read, list, run, observe};
  }
  return {create};
});

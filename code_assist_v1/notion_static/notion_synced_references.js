(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.NotionSyncedReferences = api;
})(typeof globalThis === 'object' ? globalThis : this, function () {
  'use strict';
  const contract = 'code_assist.notion.synced_block.v1';
  const explicit = block => block?.type === 'synced_block'
    && Object.prototype.hasOwnProperty.call(block.props || {}, 'synced_from');
  const reference = block => explicit(block) && block.props.synced_from !== null;
  const endpoint = value => value && typeof value.document_id === 'string' && value.document_id
    && typeof value.block_id === 'string' && value.block_id;
  const sameEndpoint = (a, b) => endpoint(a) && endpoint(b)
    && a.document_id === b.document_id && a.block_id === b.block_id;

  const layoutKeys = ['indent', 'layout_group', 'layout_column', 'layout_columns', 'tab_group', 'tab_id', 'text_color', 'background_color'];
  function original(block) {
    if (block?.type === 'synced_block' && !explicit(block)) {
      throw new Error('기존 동기화 블록의 원본 위치를 먼저 확인해야 합니다.');
    }
    if (reference(block)) throw new Error('참조 위치를 새 원본으로 바꿀 수 없습니다.');
    const next = structuredClone(block);
    next.type = 'synced_block'; next.props ||= {};
    next.props.synced_from = null;
    delete next.props.sync_id;
    return next;
  }
  function copyReference(source, copy, documentId) {
    if (source?.type !== 'synced_block') return copy;
    if (!explicit(source)) throw new Error('기존 동기화 블록의 원본 위치를 먼저 확인해야 합니다.');
    const origin = reference(source) ? source.props.synced_from : {document_id: documentId, block_id: source.id};
    if (!endpoint(origin) || reference(source) && (Object.keys(origin).length !== 2
        || sameEndpoint(origin, {document_id: documentId, block_id: source.id})
        || source.content || source.props.rich_text_html || source.props.children?.length)) {
      throw new Error('동기화 원본 정보를 확인할 수 없습니다.');
    }
    return {...copy, type: 'synced_block', content: '', props: {
      ...Object.fromEntries(layoutKeys.filter(key => Object.hasOwn(copy.props || {}, key)).map(key => [key, structuredClone(copy.props[key])])),
      synced_from: {document_id: origin.document_id, block_id: origin.block_id},
    }};
  }

  // The controller owns fetched display state only. It never modifies blocks,
  // drafts or the editor's CRDT. Each mount belongs to one authenticated page.
  function create(host) {
    let generation = 0, scope = null, slots = [], controller = null, flight = null;
    const watches = new Map(), revisions = new Map(), dirty = new Set(), retries = new Map();
    const schedule = host.schedule || setTimeout, cancel = host.cancel || clearTimeout;
    const current = (version, context) => generation === version && scope === context && host.current(context);
    function clear() {
      generation += 1;
      controller?.abort(); controller = null; flight = null;
      for (const record of watches.values()) record.stop?.();
      watches.clear(); dirty.clear(); revisions.clear();
      for (const timer of retries.values()) cancel(timer);
      retries.clear();
      for (const slot of slots) host.display(slot, {phase: 'idle'});
    }
    function dispose() { clear(); scope = null; slots = []; }
    function invalidate(slot) {
      revisions.set(slot, (revisions.get(slot) || 0) + 1);
      host.display(slot, {phase: 'loading'});
      dirty.add(slot);
    }
    function observe(data, slot, version, context) {
      if (!host.watch) return true;
      const documents = new Set([context.pageId, slot.origin.document_id]);
      if (!Array.isArray(data.witnesses) || data.witnesses.length !== documents.size) throw new Error('Missing synced witnesses');
      const remaining = new Set(documents);
      for (const witness of data.witnesses) {
        if (!remaining.delete(witness?.document_id) || typeof witness.workspace_id !== 'string' || !witness.workspace_id
            || witness.document_id === context.pageId && witness.workspace_id !== context.workspaceId
            || !['document_sequence', 'workspace_epoch', 'document_epoch', 'generation'].every(key => Number.isSafeInteger(witness[key]) && witness[key] >= 1)
            || watches.has(witness.document_id) && watches.get(witness.document_id).workspaceId !== witness.workspace_id) {
          throw new Error('Invalid synced witness');
        }
      }
      let fresh = true;
      for (const witness of data.witnesses) {
        let record = watches.get(witness.document_id);
        if (!record) {
          record = {workspaceId: witness.workspace_id, sequence: witness.document_sequence, slots: new Set(), available: null, stop: null};
          watches.set(witness.document_id, record);
          record.slots.add(slot);
          record.stop = host.watch(context, witness, event => {
            if (!current(version, context) || !host.visible() || watches.get(witness.document_id) !== record) return;
            const recovering = event.type === 'available' && record.available === false;
            const unavailable = event.type === 'unavailable' && record.available !== false;
            if (event.type === 'unavailable') record.available = false;
            else if (event.type === 'available') record.available = true;
            if (Number.isSafeInteger(event.sequence)) record.sequence = Math.max(record.sequence, event.sequence);
            if (event.type === 'changed' || recovering || unavailable) {
              for (const target of record.slots) invalidate(target);
              void run();
            }
          });
        } else record.slots.add(slot);
        fresh &&= witness.document_sequence >= record.sequence;
      }
      return fresh;
    }
    async function run() {
      if (!scope || !host.current(scope) || !host.visible()) { clear(); return; }
      if (flight) return flight;
      const version = generation, context = scope;
      controller = new AbortController();
      const signal = controller.signal;
      // Limit simultaneous materializations even on a page with many instances.
      let resolveTask, rejectTask;
      const task = flight = new Promise((resolve, reject) => {resolveTask = resolve; rejectTask = reject;});
      void (async () => {
       while (dirty.size && current(version, context) && !signal.aborted) {
        const pending = [...dirty].filter(slot => host.visible(slot));
        dirty.clear();
        let index = 0;
        async function worker() {
          while (index < pending.length && current(version, context) && !signal.aborted) {
            const slot = pending[index++];
            const revision = revisions.get(slot) || 0;
            host.display(slot, {phase: 'loading'});
            try {
              const data = await host.load(context, slot.id, signal);
              if (!current(version, context) || signal.aborted) return;
              if ((revisions.get(slot) || 0) !== revision) continue;
              if (data?.contract !== contract
                  || !sameEndpoint(data.instance, {document_id: context.pageId, block_id: slot.id})
                  || !sameEndpoint(data.origin, slot.origin)
                  || data.block?.id !== data.origin.block_id
                  || data.block?.type !== 'synced_block' || !explicit(data.block)
                  || data.block.props.synced_from !== null) throw new Error('Invalid synced response');
              if (!observe(data, slot, version, context)) {
                // A newer event can overtake a GET. Retry without spinning on
                // an old response or starting unbounded parallel reads.
                if (!retries.has(slot)) retries.set(slot, schedule(() => {
                  retries.delete(slot);
                  if (current(version, context) && host.visible()) {invalidate(slot); void run();}
                }, 650));
                continue;
              }
              if ((revisions.get(slot) || 0) === revision) {
                cancel(retries.get(slot)); retries.delete(slot);
                host.display(slot, {phase: 'ready', data});
              }
            } catch (error) {
              if (!current(version, context) || signal.aborted) return;
              if ((revisions.get(slot) || 0) !== revision) continue;
              host.display(slot, {phase: 'error', denied: [401, 403, 404].includes(error?.status)});
            }
          }
        }
        await Promise.all([worker(), worker()]);
       }
      })().then(resolveTask, rejectTask);
      try { await task; } finally {
        if (flight === task) {
          flight = null; controller = null;
          if (dirty.size) void run();
        }
      }
    }
    function refresh(options = {}) {
      if (!scope || !host.current(scope) || !host.visible()) { clear(); return Promise.resolve(); }
      if (flight && !options.afterFlight) return flight;
      for (const slot of slots.filter(slot => host.visible(slot))) invalidate(slot);
      return run();
    }
    function mount(context, nextSlots) {
      dispose(); scope = context; slots = nextSlots;
      return refresh();
    }
    return {mount, refresh, clear, dispose};
  }
  return {explicit, reference, original, copyReference, create};
});

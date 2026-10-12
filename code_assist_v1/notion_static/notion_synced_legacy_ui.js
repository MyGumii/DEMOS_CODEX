(function (root) {
  'use strict';
  root.NotionSyncedLegacyUi = {create(host) {
    const controller = root.NotionSyncedLegacyAdoption.create({
      storage: () => root.localStorage, locks: () => root.navigator.locks,
      transport: host.transport, changed: () => render(),
    });
    const same = scope => host.enabled() && scope.userId === host.scope().userId && scope.workspaceId === host.scope().workspaceId;
    const base = scope => `/api/notion/workspaces/${encodeURIComponent(scope.workspaceId)}/synced-legacy/`;
    const endpointKey = item => JSON.stringify([item.document_id, item.block_id]);
    const element = (tag, text, className) => {
      const result = document.createElement(tag);
      if (text !== undefined) result.textContent = text;
      if (className) result.className = className;
      return result;
    };
    function button(parent, text, action) {
      const result = element('button', text); result.type = 'button';
      result.addEventListener('click', action); parent.append(result); return result;
    }
    async function perform(scope, request, action, current) {
      let release = () => {};
      try {
        const outcome = await controller.run(scope, request, {action, current,
          beforeWrite: async () => { release = await host.flush(); if (!current()) throw Error('화면이 바뀌었습니다.'); },
        });
        if (current()) {
          if (outcome?.state === 'committed') {
            host.notice('선택한 동기화 블록의 원본 지정이 저장되었습니다.', 'ok');
            if (action === 'adopt') await host.refresh(request);
          } else if (outcome?.state === 'cancelled') host.notice('전환 요청이 취소되었습니다.', 'ok');
          else host.notice(outcome?.state === 'busy' ? '다른 탭에서 같은 요청을 처리하고 있습니다.' : '완료된 결과가 아직 없습니다. 같은 요청을 확인하거나 취소해 주세요.', 'error');
        }
        return outcome;
      } finally { release(); render(); }
    }
    function render() {
      const banner = document.querySelector('#syncedLegacyStatus');
      if (!banner) return;
      banner.replaceChildren(); banner.hidden = true;
      if (!host.enabled()) return;
      const scope = host.scope();
      let pending;
      try { pending = controller.list(scope).filter(item => item.state === 'pending'); }
      catch (error) { banner.textContent = error.message; banner.hidden = false; return; }
      if (!pending.length) return;
      banner.hidden = false;
      for (const entry of pending) {
        const row = element('div', undefined, 'mini-row');
        row.append(element('span', '동기화 원본 전환의 결과를 확인해 주세요.'));
        for (const [text, action] of [['결과 확인', 'result'], ['같은 요청 재시도', 'adopt'], ['요청 취소', 'cancel']]) {
          const control = button(row, text, async () => {
            if (!same(scope)) return;
            control.disabled = true;
            try { await perform(scope, entry.request, action, () => same(scope)); }
            catch (error) { if (same(scope)) host.notice(error.message, 'error'); }
            finally { if (control.isConnected) control.disabled = false; }
          });
          control.disabled = entry.busy;
        }
        banner.append(row);
      }
    }
    async function open(anchor) {
      if (!host.enabled()) return;
      const scope = host.scope(), activePage = host.activePage(), generation = host.current();
      // Save the editor before reviewing its authoritative content.
      let release = () => {};
      try { release = await host.flush(); }
      catch (error) { host.notice(error.message, 'error'); return; }
      finally { release(); }
      if (!same(scope) || !generation() || host.activePage() !== activePage) return;
      host.modal('동기화 원본 지정', '<div id="syncedLegacyChooser" class="synced-legacy-chooser"></div>');
      const root = document.querySelector('#syncedLegacyChooser');
      const current = () => same(scope) && generation() && host.activePage() === activePage && root.isConnected && host.modalVisible();
      const intro = element('p', '원본 블록을 직접 고르고, 같은 원본을 사용할 블록을 선택해 주세요. 선택한 블록만 전환됩니다.');
      const form = element('div'), status = element('p'); status.setAttribute('role', 'status');
      const pages = element('select'); pages.setAttribute('aria-label', '동기화 블록을 확인할 페이지');
      const candidates = element('div', undefined, 'synced-legacy-candidates');
      const summary = element('p');
      const choices = new Map(), references = new Map(); let origin = null, cursor = '', serial = 0;
      for (const page of host.pages()) {
        const option = element('option', page.title || '제목 없음'); option.value = page.id; pages.append(option);
      }
      pages.value = anchor.document_id;
      const more = button(form, '후보 더 보기', () => load(true)); more.hidden = true;
      const review = button(form, '선택 검토', () => preview()); review.disabled = true;
      form.prepend(pages, candidates, summary); root.append(intro, form, status);
      function update() {
        summary.textContent = `원본 ${origin ? '1개 선택' : '미선택'} · 함께 전환할 블록 ${references.size}개`;
        review.disabled = !origin || Boolean(choices.get(endpointKey(origin))?.original_only && references.size === 0);
        for (const row of candidates.children) {
          const choice = choices.get(row.dataset.choice);
          const original = row.querySelector('[data-legacy-origin]');
          const reference = row.querySelector('[data-legacy-reference]');
          original.checked = origin && endpointKey(origin) === row.dataset.choice;
          reference.checked = references.has(row.dataset.choice);
          reference.disabled = Boolean(choice.unavailable_reason || original.checked || choice.original_only);
        }
      }
      async function load(append = false) {
        if (!current()) return;
        const turn = ++serial, documentId = pages.value;
        more.disabled = true; status.textContent = '블록 후보를 확인하는 중…';
        if (!append) { cursor = ''; candidates.replaceChildren(); }
        try {
          const data = await host.transport(base(scope) + 'candidates', {method: 'POST', body: {anchor, document_id: documentId, cursor}});
          if (!current() || turn !== serial) return;
          if (data?.contract !== 'code_assist.notion.synced_legacy_adoption_candidates.v1'
              || data.workspace_id !== scope.workspaceId || data.document_id !== documentId || !Array.isArray(data.items)
              || !(data.next_cursor === null || typeof data.next_cursor === 'string')) throw Error('블록 후보의 범위를 확인하지 못했습니다.');
          for (const choice of data.items) {
            if (choice.endpoint?.document_id !== documentId || typeof choice.endpoint.block_id !== 'string') throw Error('블록 후보의 위치를 확인하지 못했습니다.');
            const key = endpointKey(choice.endpoint); choices.set(key, choice);
            const row = element('div', undefined, 'synced-legacy-choice'); row.dataset.choice = key;
            row.dataset.legacyBlock = choice.endpoint.block_id;
            row.append(element('p', choice.content_preview || '빈 동기화 블록'));
            const originalLabel = element('label'), original = element('input');
            original.type = 'radio'; original.name = 'legacy-original'; original.dataset.legacyOrigin = '';
            original.disabled = Boolean(choice.unavailable_reason);
            originalLabel.append(original, document.createTextNode(choice.original_only ? '기존 원본 사용' : '원본으로 지정'));
            original.addEventListener('change', () => { origin = choice.endpoint; references.delete(key); update(); });
            const referenceLabel = element('label'), reference = element('input');
            reference.type = 'checkbox'; reference.dataset.legacyReference = '';
            referenceLabel.append(reference, document.createTextNode('함께 전환'));
            reference.addEventListener('change', () => {
              if (reference.checked && references.size >= 127) {
                reference.checked = false; status.textContent = '한 번에 함께 전환할 블록은 127개까지 선택할 수 있습니다.'; return;
              }
              if (reference.checked) references.set(key, choice.endpoint); else references.delete(key);
              update();
            });
            row.append(originalLabel, referenceLabel);
            if (choice.unavailable_reason) row.append(element('small', choice.unavailable_reason));
            candidates.append(row);
          }
          cursor = data.next_cursor || ''; more.hidden = !cursor;
          status.textContent = candidates.children.length ? '' : '이 페이지에는 같은 그룹의 동기화 블록이 없습니다.';
          update();
        } catch (error) { if (current() && turn === serial) status.textContent = error.message; }
        finally { if (turn === serial) more.disabled = false; }
      }
      async function preview() {
        if (!current() || !origin) return;
        review.disabled = true; form.inert = true; status.textContent = '선택한 내용을 검토하는 중…';
        try {
          const data = await host.transport(base(scope) + 'prepare', {method: 'POST', body: {origin, references: [...references.values()]}});
          if (!current()) return;
          const request = {origin: data.origin, references: data.references, preparation_sha256: data.preparation_sha256,
            operation_id: host.operationId()};
          if (data?.contract !== 'code_assist.notion.synced_legacy_adoption_review.v1' || data.workspace_id !== scope.workspaceId
              || !window.NotionSyncedLegacyAdoption.validRequest(request)
              || endpointKey(request.origin) !== endpointKey(origin)
              || JSON.stringify(request.references.map(endpointKey).sort()) !== JSON.stringify([...references.keys()].sort())) throw Error('검토 결과가 선택한 블록과 일치하지 않습니다.');
          const confirmation = element('div');
          const reuse = data.original.already_designated === true;
          confirmation.append(element('p', `원본: ${data.original.title || '제목 없음'}`),
            element('pre', data.original.content_preview || '빈 동기화 블록', 'synced-legacy-preview'),
            element('p', reuse ? `기존 원본에 선택한 참조 ${request.references.length}개를 연결합니다. 원본 내용은 그대로 유지됩니다.`
              : `${data.document_count}개 페이지에서 원본 1개와 참조 ${request.references.length}개를 전환합니다. 선택하지 않은 블록은 포함되지 않습니다.`));
          if (data.original.preview_truncated) confirmation.append(element('small', '미리보기는 앞부분만 표시합니다. 원문은 그대로 보존됩니다.'));
          button(confirmation, '선택으로 돌아가기', () => { if (current()) { confirmation.remove(); form.hidden = false; form.inert = false; update(); } });
          const confirm = button(confirmation, reuse ? '기존 원본에 연결' : '원본 지정 및 전환', async () => {
            if (!current()) return;
            confirm.disabled = true; status.textContent = '전환 요청을 저장하는 중…';
            try {
              const result = await perform(scope, request, 'adopt', current);
              if (current()) status.textContent = result?.state === 'committed' ? '원본 지정이 저장되었습니다.' : '요청 결과를 확인해 주세요.';
            } catch (error) { if (current()) status.textContent = error.message + ' 아래 전환 요청에서 결과를 확인하거나 취소해 주세요.'; }
          });
          confirm.className = 'primary';
          form.hidden = true; root.insertBefore(confirmation, status); status.textContent = '';
        } catch (error) { if (current()) { form.inert = false; status.textContent = error.message; update(); } }
      }
      pages.addEventListener('change', () => load());
      await load();
    }
    async function history() {
      if (!host.enabled()) return;
      const scope = host.scope(), generation = host.current();
      host.modal('동기화 전환 요청 확인', '<div id="syncedLegacyHistory" class="synced-legacy-chooser"></div>');
      const root = document.querySelector('#syncedLegacyHistory');
      const current = () => same(scope) && generation() && root.isConnected && host.modalVisible();
      const status = element('p'), items = element('div'); status.setAttribute('role', 'status');
      root.append(element('p', '브라우저에 기록이 없어도 서버에 등록된 내 전환 요청을 확인할 수 있습니다.'), status, items);
      let cursor = '', busy = false;
      const seen = new Set();
      const more = button(root, '더 보기', load); more.hidden = true;
      async function load() {
        if (!current() || busy) return;
        busy = true; more.disabled = true; status.textContent = '전환 요청을 확인하는 중…';
        try {
          const data = await host.transport(base(scope) + 'requests?limit=20' + (cursor ? '&cursor=' + encodeURIComponent(cursor) : ''), {cache: 'no-store'});
          if (!current()) return;
          if (data?.contract !== 'code_assist.notion.synced_legacy_adoption_history.v1' || data.workspace_id !== scope.workspaceId
              || !Array.isArray(data.items) || !(data.next_cursor === null || typeof data.next_cursor === 'string')) throw Error('전환 요청을 확인하지 못했습니다.');
          for (const entry of data.items) {
            if (!window.NotionSyncedLegacyAdoption.validRequest(entry.request) || entry.operation_id !== entry.request.operation_id
                || !['pending', 'committed', 'cancelled'].includes(entry.state)) throw Error('전환 요청의 범위를 확인하지 못했습니다.');
            if (seen.has(entry.operation_id)) continue; seen.add(entry.operation_id);
            const row = element('div', undefined, 'synced-legacy-choice');
            const label = element('p', `${{pending:'진행 중',committed:'완료',cancelled:'취소'}[entry.state]} · 참조 ${entry.request.references.length}개`); row.append(label);
            for (const [text, action] of [['결과 확인', 'result'], ...(entry.state === 'pending' ? [['같은 요청 재시도', 'adopt'], ['요청 취소', 'cancel']] : [])]) {
              const control = button(row, text, async () => {
                if (!current()) return;
                row.inert = true;
                try {
                  const outcome = await perform(scope, entry.request, action, current);
                  if (current()) label.textContent = outcome?.state === 'committed' ? '원본 지정이 저장되었습니다.' : outcome?.state === 'cancelled' ? '전환 요청이 취소되었습니다.' : '완료된 결과가 아직 없습니다.';
                  if (current() && ['committed', 'cancelled'].includes(outcome?.state))
                    for (const item of row.querySelectorAll('button')) if (item.textContent !== '결과 확인') item.hidden = true;
                } catch (error) { if (current()) status.textContent = error.message; }
                finally { row.inert = false; }
              });
            }
            items.append(row);
          }
          cursor = data.next_cursor || ''; more.hidden = !cursor;
          status.textContent = seen.size ? '' : '등록된 전환 요청이 없습니다.';
        } catch (error) { if (current()) { status.textContent = error.message; more.hidden = false; more.textContent = '다시 확인'; } }
        finally { busy = false; more.disabled = false; }
      }
      await load();
    }
    return {open, history, render, observe: controller.observe, pending: () => host.enabled() && controller.pending(host.scope())};
  }};
})(window);

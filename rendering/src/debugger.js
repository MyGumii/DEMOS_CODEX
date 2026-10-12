import './debugger.css';

const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const activeStatus = status => ['starting', 'running', 'paused'].includes(status);
const kindNames = { line: '줄 실행', call: '함수 진입', return: '함수 반환', exception: '예외', http: 'HTTP', sse: 'SSE', checkpoint: '처리 단계', stop: '중단', stopped: '중단', output: '출력', request: '요청', response: '응답' };
const statusNames = { starting: '실행 준비 중', running: '코드 실행 중', paused: 'Breakpoint에서 중단', completed: '실행 완료', failed: '실행 오류', stopped: '실행 종료' };
const brief = value => typeof value === 'string' ? value : JSON.stringify(value);
const sameValue = (a, b) => JSON.stringify(a) === JSON.stringify(b);

// The viewer consumes copied states. Playback never calls a debugger action.
export function createDebugger({ onLocation = () => {}, onClose = () => {}, toast = () => {} } = {}) {
  const root = document.createElement('section');
  root.id = 'debugger-workbench'; root.className = 'debugger-workbench'; root.hidden = true;
  root.setAttribute('aria-label', '실제 디버거와 쿼리 재생');
  root.innerHTML = `
    <header class="dbg-heading"><div><span class="dbg-eyebrow">CODE IN MOTION</span><h2>코드 실행을 눈으로 따라가기</h2></div><div class="dbg-mode" role="tablist" aria-label="실행 방식"><button id="debug-mode" role="tab" aria-selected="true">실제 디버깅</button><button id="playback-mode" role="tab" aria-selected="false">녹화 · 재생</button></div><button id="debug-close" class="dbg-icon" aria-label="실행 화면 닫기">×</button></header>
    <form id="debug-form" class="dbg-query"><label class="dbg-target-label">실행 경로<select id="debug-target" aria-label="실행 경로"><option>연결 중…</option></select></label><label class="dbg-query-label">사용자 질문<input id="debug-query" aria-label="사용자 질문" value="저장소 검색을 쉽게 설명해 주세요." autocomplete="off" /></label><button id="debug-start" class="dbg-primary" type="submit">디버거 시작</button><button id="record-start" class="dbg-primary" type="button" hidden>쿼리 녹화</button><details class="dbg-conditions"><summary id="debug-target-note" class="dbg-target-note">격리된 원본 함수 실행 · 외부 API는 모의 응답 · 조건 보기</summary><div id="debug-target-assumptions"></div></details></form>
    <div class="dbg-control-bar"><div class="dbg-status"><i id="debug-status-dot"></i><strong id="debug-status">실행 전</strong><span id="debug-status-description">줄 번호 옆을 클릭해 breakpoint를 설정하세요.</span></div><div id="debug-actions" class="dbg-actions"><button id="debug-action-continue" data-debug-action="continue" title="중단한 코드 계속 실행">▶ 계속</button><button id="debug-action-next" data-debug-action="next" title="현재 줄을 실행하고 다음 줄에서 중단">↷ 다음 줄</button><button id="debug-action-stepIn" data-debug-action="stepIn" title="호출하는 함수 안으로 진입">↓ 함수 안으로</button><button id="debug-action-stepOut" data-debug-action="stepOut" title="현재 함수에서 나와 호출 위치로 이동">↑ 함수 밖으로</button><button id="debug-action-pause" data-debug-action="pause" title="실행 중인 코드 일시 중단">Ⅱ 중단</button><button id="debug-action-stop" data-debug-action="stop" title="전용 실행 프로세스 종료">■ 종료</button></div><div id="playback-actions" class="dbg-actions" hidden><button id="playback-previous" aria-label="이전 기록">← 이전</button><button id="playback-play">▶ 재생</button><button id="playback-next" aria-label="다음 기록">다음 →</button><select id="playback-speed" aria-label="재생 속도"><option value="1">1×</option><option value="2">2×</option><option value="4">4×</option></select><select id="playback-granularity" aria-label="재생 기록 상세 수준"><option value="major">주요 동작</option><option value="line">줄 단위</option></select></div></div>
    <div id="debug-error" class="dbg-error" role="alert" hidden></div>
    <div id="debug-recordings-bar" class="dbg-recordings" hidden><label>저장된 실행<select id="playback-recordings" aria-label="저장된 실행"><option value="">기록 선택</option></select></label><button id="playback-refresh" title="저장된 실행 새로고침">↻</button><span>재생은 저장된 값만 읽습니다.</span></div>
    <nav class="dbg-mobile-tabs" aria-label="실행 정보 패널"><button data-debug-panel="code" class="active">코드</button><button data-debug-panel="variables">변수</button><button data-debug-panel="stack">호출 스택</button></nav>
    <div class="dbg-body" data-panel="code"><section class="dbg-code-pane"><div class="dbg-panel-heading"><div><span>실행 코드</span><select id="debug-file" aria-label="소스 파일"></select></div><span id="debug-source-badge" class="dbg-source-badge">저장소 원본</span></div><div class="dbg-code-caption"><span><i class="dbg-break-legend"></i> Breakpoint</span><span><i class="dbg-current-legend"></i><span id="debug-position-note">강조된 줄은 다음에 실행할 줄</span></span></div><div id="debug-code" class="dbg-code" tabindex="0" aria-label="소스 코드와 breakpoint"><p class="dbg-empty">실행 경로를 선택하면 원본 코드가 보입니다.</p></div><div id="debug-code-location" class="dbg-code-location">실행 위치가 여기에 표시됩니다.</div></section>
    <aside class="dbg-inspection"><section class="dbg-variables-pane"><div class="dbg-panel-heading"><span>현재 변수 <small id="debug-variable-count"></small></span><span class="dbg-value-legend"><i></i> 변경된 값</span></div><div id="debug-variables" class="dbg-variables"><p class="dbg-empty">중단 시점 또는 선택한 기록의 실제 값을 표시합니다.</p></div></section><section class="dbg-stack-pane"><div class="dbg-panel-heading"><span>호출 스택</span><small id="debug-thread"></small></div><div id="debug-stack" class="dbg-stack"><p class="dbg-empty">현재 함수를 호출한 순서</p></div></section></aside></div>
    <section class="dbg-timeline-pane"><div class="dbg-timeline-heading"><div><span>실행 타임라인</span><small id="debug-event-count">0개 기록</small></div><span id="debug-cursor-label">아직 실행하지 않았습니다.</span></div><input id="debug-seek" type="range" min="0" max="0" value="0" aria-label="실행 기록 위치" disabled /><div id="debug-timeline" class="dbg-timeline"></div><div id="debug-result" class="dbg-result" hidden></div></section>`;
  document.body.append(root);
  const $ = selector => root.querySelector(selector);
  let targets = [], target = null, run = null, mode = 'debug', events = [], cursor = -1;
  let shownEvent = null, previousVariables = {}, selectedFrame = null, sourceFile = null, sourceData = null;
  let pollTimer, playTimer, playing = false, generation = 0, pollInFlight = false, busy = false, lastSignature = '', autoFollow = true;
  let initialized = false, targetBreakpoints = [], liveVariables = null, inspectedReferences = new Map(), closeRequested = false;
  const sourceCache = new Map();
  let timelineWindowStart = 0, timelineWindowEnd = 0, selectionGeneration = 0;

  const fail = error => { $('#debug-error').textContent = error.message || String(error); $('#debug-error').hidden = false; };
  async function api(path, body) {
    const response = await fetch(`/atlas-api${path}`, body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || `실행 서버 응답 오류 (${response.status})`);
    return data;
  }
  const fileIdOf = file => typeof file === 'string' ? file : file.fileId || file.id || file.path;
  const targetFiles = () => (target?.files || [target?.entry].filter(Boolean)).map(fileIdOf).filter(Boolean);
  const labelForTarget = item => item.label || item.title || item.id;
  const currentRecords = () => mode === 'debug' ? events : events.filter(event => $('#playback-granularity').value === 'line' || event.kind !== 'line');
  const isLive = () => run && activeStatus(run.status);
  const hashFor = event => event?.sourceHash || run?.sourceHashes?.[event?.fileId] || null;
  function setErrorClear() { $('#debug-error').hidden = true; }
  function stopPlayback() { clearTimeout(playTimer); playing = false; $('#playback-play').textContent = '▶ 재생'; }

  function setMode(nextMode) {
    stopPlayback(); mode = nextMode; autoFollow = true; cursor = -1; shownEvent = null; previousVariables = {}; liveVariables = null;
    $('#debug-mode').setAttribute('aria-selected', String(mode === 'debug')); $('#playback-mode').setAttribute('aria-selected', String(mode === 'record'));
    $('#debug-start').hidden = mode !== 'debug'; $('#record-start').hidden = mode !== 'record';
    $('#debug-actions').hidden = mode !== 'debug'; $('#playback-actions').hidden = mode !== 'record'; $('#debug-recordings-bar').hidden = mode !== 'record';
    root.dataset.mode = mode;
    if (mode === 'record') refreshRecordings().catch(fail);
    renderStatus(); selectEvent(0);
  }

  async function loadTargets() {
    const result = await api('/targets'); targets = result.targets || result;
    if (!Array.isArray(targets) || !targets.length) throw new Error('실행 가능한 코드 경로가 없습니다.');
    $('#debug-target').innerHTML = targets.map(item => `<option value="${esc(item.id)}">${esc(labelForTarget(item))}</option>`).join('');
    await changeTarget(targets[0].id); initialized = true;
  }
  async function changeTarget(id) {
    target = targets.find(item => item.id === id) || targets[0];
    targetBreakpoints = (target.defaultBreakpoints || target.breakpoints || []).map(item => ({ fileId: item.fileId || item.path, line: item.line, verified: false }));
    $('#debug-target').value = target.id;
    if (target.query || target.defaultQuery) $('#debug-query').value = target.query || target.defaultQuery;
    const files = targetFiles();
    $('#debug-file').innerHTML = files.map(file => `<option value="${esc(file)}">${esc(file)}</option>`).join('');
    $('#debug-target-note').textContent = `격리된 원본 함수 실행 · 외부 API는 모의 응답${target.id.includes('image') ? ' · 테스트 PNG 첨부' : ''} · 조건 보기`;
    $('#debug-target-assumptions').innerHTML = `<p>${esc(target.description || '')}</p>${(target.assumptions || []).length ? `<ul>${target.assumptions.map(item => `<li>${esc(item)}</li>`).join('')}</ul>` : ''}`;
    await loadSource(targetBreakpoints[0]?.fileId || fileIdOf(target.entry || files[0]), targetBreakpoints[0]?.line || target.entry?.line);
    renderStatus();
  }

  async function loadSource(fileId, line, expectedHash, selection = null) {
    if (!fileId) return;
    sourceFile = fileId;
    if (!sourceCache.has(fileId)) sourceCache.set(fileId, api(`/source?fileId=${encodeURIComponent(fileId)}`).catch(error => { sourceCache.delete(fileId); throw error; }));
    const loaded = await sourceCache.get(fileId);
    if (sourceFile !== fileId || (selection !== null && selection !== selectionGeneration)) return;
    sourceData = loaded;
    const selector = $('#debug-file');
    if (![...selector.options].some(option => option.value === fileId)) selector.add(new Option(fileId, fileId));
    selector.value = fileId;
    const mismatch = expectedHash && loaded.sourceHash && expectedHash !== loaded.sourceHash;
    $('#debug-source-badge').textContent = mismatch ? '기록과 소스 버전이 다름' : '저장소 원본';
    $('#debug-source-badge').classList.toggle('mismatch', Boolean(mismatch));
    const lines = loaded.lines || String(loaded.content || loaded.source || '').split('\n');
    const executable = new Set(loaded.executableLines || []);
    $('#debug-code').innerHTML = lines.map((text, index) => {
      const lineNumber = index + 1, point = targetBreakpoints.find(item => item.fileId === fileId && item.line === lineNumber);
      const allowed = !executable.size || executable.has(lineNumber);
      return `<div class="dbg-line${point ? ' has-breakpoint' : ''}${point?.verified ? ' verified' : ''}${lineNumber === line ? ' current' : ''}" data-source-line="${lineNumber}"><button class="dbg-gutter" data-debug-line="${lineNumber}" aria-label="${lineNumber}줄 breakpoint ${point ? '제거' : '설정'}" aria-pressed="${Boolean(point)}" ${!allowed ? 'disabled' : ''} title="${esc(!allowed ? '실행 문장이 없는 줄' : point?.verified ? '디버거가 확인한 breakpoint' : point ? point.message || '설정된 breakpoint · 확인 대기' : 'Breakpoint 설정')}"><i></i><span>${lineNumber}</span></button><code>${highlight(text) || ' '}</code></div>`;
    }).join('');
    updateCodeLocation({ fileId, line, function: shownEvent?.fileId === fileId ? shownEvent.function : '' });
    if (line) $('#debug-code').querySelector(`[data-source-line="${line}"]`)?.scrollIntoView({ block: 'center', inline: 'nearest', behavior: 'instant' });
  }
  function highlight(text) {
    // Token coloring is purely presentational and does not evaluate source.
    return String(text).split(/("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|#[^\n]*|\b(?:def|class|return|if|elif|else|for|while|try|except|finally|with|as|import|from|yield|async|await|raise|True|False|None|and|or|not|in|is|lambda)\b|\b\d+(?:\.\d+)?\b)/g).map(token => {
      const color = /^#/.test(token) ? 'comment' : /^["']/.test(token) ? 'string' : /^\d/.test(token) ? 'number' : /^(def|class|return|if|elif|else|for|while|try|except|finally|with|as|import|from|yield|async|await|raise|True|False|None|and|or|not|in|is|lambda)$/.test(token) ? 'keyword' : '';
      return color ? `<span class="dbg-token-${color}">${esc(token)}</span>` : esc(token);
    }).join('');
  }
  function updateCodeLocation(event) {
    $('#debug-code-location').textContent = event?.fileId ? `${event.fileId}:${event.line || '—'} · ${event.function || '실행 위치'}` : sourceFile || '실행 위치가 여기에 표시됩니다.';
  }

  async function toggleBreakpoint(line) {
    const index = targetBreakpoints.findIndex(item => item.fileId === sourceFile && item.line === line);
    if (index >= 0) targetBreakpoints.splice(index, 1); else targetBreakpoints.push({ fileId: sourceFile, line, verified: false });
    if (isLive() && run.mode === 'debug') {
      try {
        const result = await api(`/runs/${encodeURIComponent(run.id)}/breakpoints`, { breakpoints: targetBreakpoints.map(({ fileId, line }) => ({ fileId, line })) });
        acceptBreakpoints(result.breakpoints || result);
      } catch (error) { fail(error); }
    }
    await loadSource(sourceFile, shownEvent?.fileId === sourceFile ? shownEvent.line : null, hashFor(shownEvent));
  }
  function acceptBreakpoints(points) {
    if (!Array.isArray(points)) return;
    targetBreakpoints = points.map(point => ({ ...point, fileId: point.fileId || point.path || point.source?.path }));
  }

  function renderStatus() {
    const live = isLive(), paused = run?.status === 'paused';
    const pauseLabel = run?.stop?.reason === 'step' ? '한 줄 실행 후 중단' : run?.stop?.reason === 'pause' ? '일시 중단' : run?.stop?.reason === 'exception' ? '예외에서 중단' : 'Breakpoint에서 중단';
    $('#debug-status').textContent = run?.status === 'paused' ? pauseLabel : run ? statusNames[run.status] || run.status : '실행 전';
    $('#debug-status-dot').dataset.state = run?.status || 'idle';
    const outsideTarget = mode === 'debug' && paused && run.stop && (run.stop.outside === true || !run.stop.frames?.length);
    $('#debug-status-description').textContent = outsideTarget ? '관찰 대상 밖의 호출자로 이동했습니다. 계속 실행하거나 종료할 수 있습니다.' : mode === 'record' && !live ? '복사해 저장한 상태를 재생합니다. 코드는 다시 실행되지 않습니다.' : paused ? '실제 코드가 멈췄습니다. 강조된 줄은 다음에 실행됩니다.' : live ? '전용 프로세스에서 원본 함수를 실행하고 있습니다.' : '줄 번호 옆을 클릭해 breakpoint를 설정하세요.';
    $('#debug-start').disabled = busy || live || !target; $('#record-start').disabled = busy || live || !target;
    $('#debug-close').disabled = busy;
    $('#debug-target').disabled = live || busy; $('#debug-query').disabled = live || busy;
    $('#debug-mode').disabled = live || busy; $('#playback-mode').disabled = live || busy;
    root.querySelectorAll('[data-debug-action]').forEach(button => {
      const action = button.dataset.debugAction;
      button.disabled = busy || !live || (['continue', 'next', 'stepIn', 'stepOut'].includes(action) && !paused) || (action === 'pause' && paused);
    });
    const records = currentRecords();
    $('#playback-previous').disabled = cursor <= 0 || !records.length;
    $('#playback-next').disabled = cursor >= records.length - 1 || !records.length;
    $('#playback-play').disabled = !records.length || live;
    $('#debug-seek').disabled = !records.length || (live && mode === 'debug');
    $('#playback-recordings').disabled = live;
  }

  async function startRun() {
    if (isLive() || busy || !target) return;
    setErrorClear(); stopPlayback(); busy = true; renderStatus();
    try {
      const result = await api('/runs', { mode, target: target.id, query: $('#debug-query').value, breakpoints: targetBreakpoints.map(({ fileId, line }) => ({ fileId, line })) });
      run = result.run || result; events = run.events || []; cursor = -1; shownEvent = null; previousVariables = {}; inspectedReferences.clear(); selectedFrame = null; liveVariables = null; autoFollow = true; lastSignature = '';
      acceptBreakpoints(run.breakpoints); updateRun(run); startPolling();
      if (mode === 'record') refreshRecordings().catch(fail);
    } catch (error) { fail(error); }
    busy = false; renderStatus(); if (closeRequested) close();
  }
  async function sendAction(action) {
    if (!run || busy) return;
    setErrorClear(); busy = true; renderStatus();
    try {
      const response = await api(`/runs/${encodeURIComponent(run.id)}/action`, { action });
      if (response.run || response.id) updateRun(response.run || response);
      else await pollRun();
      selectedFrame = null; liveVariables = null; inspectedReferences.clear(); autoFollow = true;
    } catch (error) { fail(error); }
    busy = false; renderStatus(); if (closeRequested) close();
  }
  function startPolling() {
    clearTimeout(pollTimer); generation += 1;
    const currentGeneration = generation;
    const tick = async () => {
      if (root.hidden || currentGeneration !== generation) return;
      await pollRun();
      if (isLive() && currentGeneration === generation) pollTimer = setTimeout(tick, 500);
      else if (mode === 'record') refreshRecordings().catch(fail);
    };
    pollTimer = setTimeout(tick, 200);
  }
  async function pollRun() {
    if (!run || pollInFlight) return;
    const id = run.id; pollInFlight = true;
    try {
      const result = await api(`/runs/${encodeURIComponent(id)}`);
      if (run?.id === id) updateRun(result.run || result);
    } catch (error) { fail(error); }
    finally { pollInFlight = false; }
  }
  function updateRun(nextRun) {
    run = nextRun; events = run.events || [];
    acceptBreakpoints(run.breakpoints);
    const signature = `${run.id}:${run.status}:${events.length}:${run.stop?.frameId || ''}:${JSON.stringify(run.breakpoints || [])}`;
    if (signature !== lastSignature) {
      const records = currentRecords();
      if (autoFollow || cursor < 0) cursor = mode === 'debug' || activeStatus(run.status) ? records.length - 1 : 0;
      lastSignature = signature;
      renderTimeline(); selectEvent(cursor);
      if (run.error) fail(typeof run.error === 'string' ? new Error(run.error) : new Error(run.error.message || JSON.stringify(run.error)));
      renderResult();
    }
    renderStatus();
  }

  function renderTimeline() {
    const records = currentRecords();
    $('#debug-event-count').textContent = `${records.length.toLocaleString('ko-KR')}개${mode === 'record' && $('#playback-granularity').value === 'major' ? ' 주요' : ''} 기록 · ${events.length.toLocaleString('ko-KR')}개 전체`;
    $('#debug-seek').max = String(Math.max(0, records.length - 1));
    timelineWindowStart = Math.max(0, Math.min(Math.max(0, cursor - 100), records.length - 400));
    timelineWindowEnd = Math.min(records.length, timelineWindowStart + 400);
    $('#debug-timeline').innerHTML = records.slice(timelineWindowStart, timelineWindowEnd).map((event, offset) => {
      const index = timelineWindowStart + offset;
      return `<button data-debug-event="${index}" class="dbg-event ${event.kind === 'exception' ? 'exception' : ''}${index === cursor ? ' selected' : ''}" aria-label="${index + 1}번째 ${esc(kindNames[event.kind] || event.kind)} ${esc(event.function || '')}" title="${esc(event.fileId || '')}:${event.line || ''}"><span>${esc(kindNames[event.kind] || event.kind)}</span><strong>${esc(event.function || event.detail?.label || event.fileId?.split('/').at(-1) || '실행')}</strong><small>${Number(event.timeMs || 0).toFixed(0)} ms · ${event.line ? `L${event.line}` : '—'}</small></button>`;
    }).join('');
  }
  async function selectEvent(index) {
    const selection = ++selectionGeneration;
    const records = currentRecords();
    if (!records.length) { cursor = -1; renderStatus(); return; }
    cursor = Math.max(0, Math.min(index, records.length - 1));
    const event = records[cursor];
    if (!event) return;
    if (cursor < timelineWindowStart || cursor >= timelineWindowEnd) renderTimeline();
    const eventChanged = shownEvent?.seq !== event.seq || shownEvent?.runId !== event.runId;
    if (eventChanged) {
      previousVariables = shownEvent?.variables || {};
      selectedFrame = null; liveVariables = null; inspectedReferences.clear();
    }
    shownEvent = event;
    $('#debug-seek').value = String(cursor);
    $('#debug-cursor-label').textContent = `${cursor + 1} / ${records.length} · ${kindNames[event.kind] || event.kind} · ${Number(event.timeMs || 0).toFixed(0)} ms`;
    root.querySelectorAll('[data-debug-event]').forEach(button => button.classList.toggle('selected', Number(button.dataset.debugEvent) === cursor));
    renderVariables(liveVariables || event.variables || {}); renderStack(event);
    const beforeLine = event.kind === 'line' || (event.kind === 'stop' && event.detail?.reason !== 'exception');
    $('#debug-position-note').textContent = event.kind === 'checkpoint' || (event.kind === 'sse' && !event.stack?.length) ? '처리 결과 snapshot · 강조된 줄은 설명용 코드 근거' : beforeLine ? '강조된 줄은 다음에 실행할 줄' : event.kind === 'exception' || event.detail?.reason === 'exception' ? '강조된 줄은 예외가 관찰된 줄' : '강조된 줄은 이 동작이 관찰된 위치';
    if (mode === 'debug' && run?.status === 'paused' && run.stop && (run.stop.outside === true || !run.stop.frames?.length)) {
      $('#debug-code').querySelectorAll('.current').forEach(line => line.classList.remove('current'));
      $('#debug-code-location').textContent = '현재 실행 위치는 관찰 대상 밖입니다. 위 코드는 마지막으로 관찰한 파일입니다.';
      $('#debug-source-badge').textContent = '관찰 대상 밖에서 중단';
      $('#debug-variables').innerHTML = '<p class="dbg-empty">이 실행 위치는 선택한 원본 코드 범위 밖입니다. 계속 실행하면 다음 breakpoint로 이동합니다.</p>';
      $('#debug-variable-count').textContent = '—';
      $('#debug-position-note').textContent = '현재 실행 위치는 관찰 대상 밖';
      onLocation({ kind: 'outside', fileId: null, runId: run.id });
      renderStatus(); return;
    }
    const stackFrame = mode === 'debug' && run?.status === 'paused' ? run.stop?.frames?.[0] : null;
    const location = stackFrame ? { ...event, fileId: stackFrame.fileId, line: stackFrame.line, function: stackFrame.name } : event;
    if (location.fileId) {
      try {
        if (sourceFile !== location.fileId || eventChanged) await loadSource(location.fileId, location.line, hashFor(event), selection);
        else updateCodeLocation(location);
        if (selection !== selectionGeneration || root.hidden) return;
        onLocation(location);
      } catch (error) { if (selection === selectionGeneration) fail(error); }
    }
    if (eventChanged) root.querySelector(`[data-debug-event="${cursor}"]`)?.scrollIntoView({ block: 'nearest', inline: 'nearest', behavior: 'instant' });
    renderStatus();
  }
  function renderResult() {
    const result = $('#debug-result');
    result.hidden = !run?.result || activeStatus(run.status);
    if (!result.hidden) {
      const answer = run.result.answer || (run.result.status ? `HTTP ${run.result.status} · 원시 SSE 중계 완료` : null);
      result.innerHTML = `<strong>실행 결과</strong><span>${esc(answer || (typeof run.result === 'string' ? run.result : brief(run.result)))}${Number.isFinite(run.result.durationMs) ? ` · ${Number(run.result.durationMs).toFixed(0)} ms` : ''}</span>`;
    }
  }
  function renderVariables(variables) {
    const entries = Array.isArray(variables) ? variables.map(item => [item.name, item]) : Object.entries(variables || {});
    if (shownEvent?.detail && selectedFrame === null && !['stop', 'stopped', 'line', 'call', 'return'].includes(shownEvent.kind)) entries.unshift(['관측 데이터', shownEvent.detail]);
    $('#debug-variable-count').textContent = `${entries.length}개`;
    if (!entries.length) { $('#debug-variables').innerHTML = '<p class="dbg-empty">이 시점에 수집된 지역 변수가 없습니다.</p>'; return; }
    const openPaths = new Set([...$('#debug-variables').querySelectorAll('details[open][data-variable-path]')].map(item => item.dataset.variablePath));
    $('#debug-variables').innerHTML = entries.map(([name, value]) => variableMarkup(name, value, previousVariables[name], name, 0, openPaths)).join('');
  }
  function variableMarkup(name, value, previous, path, depth, openPaths) {
    const changed = shownEvent?.seq > 1 && !sameValue(previous, value), css = changed ? ' changed' : '';
    const dapRecord = value && typeof value === 'object' && 'variablesReference' in value && 'value' in value;
    const ref = dapRecord ? value.variablesReference : 0;
    const children = ref && inspectedReferences.has(ref) ? inspectedReferences.get(ref) : !dapRecord && value && typeof value === 'object' ? value : null;
    const expandable = ref || children;
    const type = dapRecord ? value.type : Array.isArray(value) ? `Array(${value.length})` : value === null ? 'null' : typeof value;
    const display = dapRecord ? value.value : expandable ? Array.isArray(value) ? `[${value.length} 항목]` : `{${Object.keys(value).length} 항목}` : brief(value);
    if (expandable && depth < 7) {
      const entries = Array.isArray(children) && children.every(item => item && typeof item === 'object' && 'variablesReference' in item && 'name' in item) ? children.map(item => [item.name, item]) : Object.entries(children || {});
      return `<details class="dbg-variable${css}" data-variable-path="${esc(path)}" ${ref ? `data-variable-ref="${ref}"` : ''} ${openPaths.has(path) || (depth === 0 && ['messages', 'payload', 'result', 'query', '관측 데이터'].includes(name)) ? 'open' : ''}><summary><span class="dbg-variable-name">${esc(name)}</span><span class="dbg-variable-type">${esc(type)}</span><span class="dbg-variable-preview">${esc(display)}</span>${changed ? '<i class="dbg-changed" title="이전 시점과 값이 달라짐"></i>' : ''}</summary><div class="dbg-variable-children">${entries.length ? entries.map(([key, item]) => variableMarkup(key, item, previous?.[key], `${path}.${key}`, depth + 1, openPaths)).join('') : ref ? '<span class="dbg-value-loading">펼치면 현재 디버거에서 조회합니다.</span>' : '<span class="dbg-value-loading">비어 있음</span>'}</div></details>`;
    }
    return `<div class="dbg-variable dbg-scalar${css}"><span class="dbg-variable-name">${esc(name)}</span><span class="dbg-variable-type">${esc(type)}</span><code class="dbg-variable-value">${esc(display)}</code>${changed ? '<i class="dbg-changed" title="이전 시점과 값이 달라짐"></i>' : ''}</div>`;
  }
  function renderStack(event) {
    const liveFrames = mode === 'debug' && run?.status === 'paused' && run.stop?.frames;
    const frames = liveFrames || playbackFrames(event);
    $('.dbg-inspection').classList.toggle('no-stack', !frames.length);
    $('.dbg-stack-pane').style.setProperty('--dbg-stack-height', `${Math.min(130, 32 + 22 * frames.length)}px`);
    $('#debug-thread').textContent = event.threadId ? `Thread ${event.threadId}` : '';
    const outside = Boolean(liveFrames && run.stop?.outside);
    $('#debug-stack').innerHTML = frames.length ? frames.map((frame, index) => `<button data-debug-frame="${index}" class="dbg-frame${selectedFrame === index || (!outside && selectedFrame === null && index === 0) ? ' selected' : ''}"><span>${!outside && index === 0 ? '현재' : `↑ ${outside ? index + 1 : index}`}</span><div><strong>${esc(frame.name || frame.function || '함수')}</strong><small>${esc(frame.fileId?.split('/').at(-1) || '외부 코드')}:${frame.line || '—'}</small></div></button>`).join('') : '<p class="dbg-empty">이 기록에는 호출 스택이 없습니다.</p>';
  }
  async function inspectFrame(index) {
    const selection = ++selectionGeneration;
    const event = shownEvent; if (!event) return;
    const live = mode === 'debug' && run?.status === 'paused';
    const frames = live ? run.stop?.frames || [] : playbackFrames(event);
    const frame = frames[index]; if (!frame) return;
    selectedFrame = index;
    if (live && frame.id !== undefined) {
      try { const response = await api(`/runs/${encodeURIComponent(run.id)}/variables?frameId=${encodeURIComponent(frame.id)}`); if (selection !== selectionGeneration || root.hidden) return; liveVariables = response.variables || response.snapshot || response; renderVariables(liveVariables); } catch (error) { if (selection === selectionGeneration) fail(error); }
    } else {
      if (index === 0 && !event.detail?.outside) renderVariables(event.variables || {});
      else { $('#debug-variable-count').textContent = '미수집'; $('#debug-variables').innerHTML = '<p class="dbg-empty">이 호출 프레임의 당시 변수는 수집되지 않았습니다. 현재 실행 프레임의 변수만 기록합니다.</p>'; }
    }
    renderStack(event);
    if (frame.fileId) { await loadSource(frame.fileId, frame.line, run?.sourceHashes?.[frame.fileId] || (frame.fileId === event.fileId ? hashFor(event) : null), selection); if (selection !== selectionGeneration || root.hidden) return; updateCodeLocation({ fileId: frame.fileId, line: frame.line, function: frame.name || frame.function }); $('#debug-position-note').textContent = '호출 스택에서 선택한 함수의 위치'; onLocation({ ...event, fileId: frame.fileId, line: frame.line, function: frame.name || frame.function, selectedFrame: true }); }
  }
  async function inspectReference(details) {
    const ref = Number(details.dataset.variableRef);
    if (!details.open || !ref || inspectedReferences.has(ref) || mode !== 'debug' || run?.status !== 'paused') return;
    const selection = selectionGeneration, runId = run.id, frameId = run.stop?.frameId;
    try {
      const response = await api(`/runs/${encodeURIComponent(run.id)}/variables?variablesReference=${ref}`);
      if (selection !== selectionGeneration || root.hidden || run?.id !== runId || run?.status !== 'paused' || run.stop?.frameId !== frameId) return;
      inspectedReferences.set(ref, response.variables || response);
      renderVariables(liveVariables || shownEvent?.variables || {});
    } catch (error) { fail(error); }
  }
  function playbackFrames(event) {
    const frames = event.stack || [];
    const last = frames.at(-1);
    return last?.fileId === event.fileId && (last?.function === event.function || last?.line === event.line) ? [...frames].reverse() : frames;
  }

  async function refreshRecordings() {
    const result = await api('/runs');
    const items = result.runs || result;
    const selector = $('#playback-recordings'), selected = run?.id || selector.value || '';
    selector.innerHTML = '<option value="">저장된 실행 선택</option>' + items.map(item => `<option value="${esc(item.id)}">${esc(labelForTarget(targets.find(target => target.id === item.target) || { id: item.target }))} · ${esc(item.mode === 'debug' ? '디버깅' : '녹화')} · ${item.eventCount || 0}개 · ${esc(item.query?.slice(0, 28) || item.createdAt || item.id)}</option>`).join('');
    if ([...selector.options].some(option => option.value === selected)) selector.value = selected;
  }
  async function loadRecording(id) {
    if (!id || isLive()) return;
    stopPlayback(); setErrorClear();
    try {
      const response = await api(`/runs/${encodeURIComponent(id)}`);
      run = response.run || response; events = run.events || []; cursor = 0; shownEvent = null; previousVariables = {}; selectedFrame = null; liveVariables = null; autoFollow = false; lastSignature = '';
      sourceCache.clear();
      const match = targets.find(item => item.id === run.target);
      if (match) await changeTarget(match.id);
      $('#debug-query').value = run.query || '';
      acceptBreakpoints(run.breakpoints); renderTimeline(); await selectEvent(0); renderResult(); renderStatus();
    } catch (error) { fail(error); }
  }
  function play() {
    if (playing) { stopPlayback(); return; }
    const records = currentRecords(); if (!records.length || isLive()) return;
    if (cursor >= records.length - 1) cursor = -1;
    autoFollow = false; playing = true; $('#playback-play').textContent = 'Ⅱ 일시정지';
    const tick = async () => {
      if (!playing || root.hidden) return;
      await selectEvent(cursor + 1);
      if (cursor >= currentRecords().length - 1) { stopPlayback(); return; }
      playTimer = setTimeout(tick, 850 / Number($('#playback-speed').value));
    };
    tick();
  }

  $('#debug-form').addEventListener('submit', event => { event.preventDefault(); startRun(); });
  $('#record-start').addEventListener('click', startRun);
  $('#debug-mode').addEventListener('click', () => setMode('debug'));
  $('#playback-mode').addEventListener('click', () => setMode('record'));
  $('#debug-target').addEventListener('change', () => {
    stopPlayback(); selectionGeneration += 1;
    run = null; events = []; cursor = -1; shownEvent = null; previousVariables = {};
    selectedFrame = null; liveVariables = null; inspectedReferences.clear(); lastSignature = ''; autoFollow = true;
    renderVariables({}); renderStack({ stack: [] }); renderTimeline(); renderResult();
    $('#debug-cursor-label').textContent = '아직 실행하지 않았습니다.';
    $('#debug-position-note').textContent = '줄 번호 옆을 클릭해 breakpoint를 설정하세요.';
    changeTarget($('#debug-target').value).catch(fail);
  });
  $('#debug-file').addEventListener('change', () => loadSource($('#debug-file').value).catch(fail));
  $('#debug-code').addEventListener('click', event => { const button = event.target.closest('[data-debug-line]'); if (button && !button.disabled) toggleBreakpoint(Number(button.dataset.debugLine)).catch(fail); });
  root.querySelectorAll('[data-debug-action]').forEach(button => button.addEventListener('click', () => sendAction(button.dataset.debugAction)));
  $('#debug-timeline').addEventListener('click', event => { const button = event.target.closest('[data-debug-event]'); if (!button || (isLive() && mode === 'debug')) return; stopPlayback(); autoFollow = false; selectEvent(Number(button.dataset.debugEvent)); });
  $('#debug-seek').addEventListener('input', () => { stopPlayback(); autoFollow = false; selectEvent(Number($('#debug-seek').value)); });
  $('#playback-previous').addEventListener('click', () => { stopPlayback(); autoFollow = false; selectEvent(cursor - 1); });
  $('#playback-next').addEventListener('click', () => { stopPlayback(); autoFollow = false; selectEvent(cursor + 1); });
  $('#playback-play').addEventListener('click', play);
  $('#playback-granularity').addEventListener('change', () => { stopPlayback(); cursor = 0; renderTimeline(); selectEvent(0); });
  $('#playback-recordings').addEventListener('change', () => loadRecording($('#playback-recordings').value));
  $('#playback-refresh').addEventListener('click', () => refreshRecordings().catch(fail));
  $('#debug-stack').addEventListener('click', event => { const button = event.target.closest('[data-debug-frame]'); if (button) inspectFrame(Number(button.dataset.debugFrame)).catch(fail); });
  $('#debug-variables').addEventListener('toggle', event => { if (event.target.matches('details[data-variable-ref]')) inspectReference(event.target); }, true);
  root.querySelectorAll('[data-debug-panel]').forEach(button => button.addEventListener('click', () => { $('.dbg-body').dataset.panel = button.dataset.debugPanel; root.querySelectorAll('[data-debug-panel]').forEach(item => item.classList.toggle('active', item === button)); }));
  $('#debug-close').addEventListener('click', close);

  async function open() {
    root.hidden = false; document.body.classList.add('debugger-open');
    try { if (!initialized) await loadTargets(); if (mode === 'record') await refreshRecordings(); if (isLive()) startPolling(); } catch (error) { fail(new Error(`로컬 실행 서버에 연결하지 못했습니다. 실행 명령을 확인하세요. ${error.message}`)); }
    renderStatus();
  }
  async function close() {
    if (busy) { closeRequested = true; return; }
    closeRequested = false;
    selectionGeneration += 1;
    stopPlayback(); generation += 1; clearTimeout(pollTimer);
    if (isLive()) {
      await sendAction('stop');
      if (!run || !['completed', 'failed', 'stopped'].includes(run.status)) {
        fail(new Error('실행 프로세스 종료를 확인하지 못했습니다. 화면을 유지합니다. 연결을 복구한 뒤 종료를 다시 눌러주세요.'));
        startPolling(); return;
      }
      toast('화면을 닫으며 전용 실행 프로세스를 종료했습니다. 기록은 보관됩니다.');
    }
    root.hidden = true; document.body.classList.remove('debugger-open'); onClose();
  }
  return { open, close, isOpen: () => !root.hidden };
}

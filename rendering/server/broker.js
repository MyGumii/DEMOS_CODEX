import http from 'node:http';
import net from 'node:net';
import path from 'node:path';
import crypto from 'node:crypto';
import fs from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { DapClient } from './dap.js';
import { isLoopbackRequest, safeCopy, safeText, sanitizeDapVariable, secretName, dapValue, plainVariableName } from './safety.js';

const defaultRendering = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const terminalStatuses = new Set(['completed', 'failed', 'stopped']);
const pythonTypes = new Set(['dict', 'list', 'tuple', 'set', 'str', 'int', 'float', 'bool', 'NoneType', 'bytes']);
const sleep = duration => new Promise(resolve => setTimeout(resolve, duration));

class ApiError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}

async function freePort() {
  const server = net.createServer();
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve); });
  const { port } = server.address();
  await new Promise(resolve => server.close(resolve));
  return port;
}

function publicRun(run) {
  return {
    id: run.id, target: run.target, mode: run.mode, status: run.status,
    query: run.query, createdAt: run.createdAt, events: run.events,
    result: run.result, error: run.error, paused: run.status === 'paused',
    stop: run.stop, breakpoints: run.breakpoints, truncated: run.truncated,
    provenance: run.provenance, sourceHashes: run.sourceHashes,
  };
}

function recordedRun(run) {
  const result = publicRun(run);
  // DAP identifiers are session-scoped. The recording stores copied values and locations only.
  result.breakpoints = result.breakpoints?.map(({ id, ...breakpoint }) => breakpoint);
  if (result.stop) {
    result.stop = { reason: result.stop.reason, description: result.stop.description,
      outside: result.stop.outside, current: result.stop.current,
      frames: result.stop.frames.map(({ fileId, line, name }) => ({ fileId, line, name })),
    };
  }
  return result;
}

async function readJsonBody(request) {
  if (!String(request.headers['content-type'] ?? '').startsWith('application/json')) throw new ApiError(415, 'JSON 요청만 지원합니다.');
  const buffers = [];
  let size = 0;
  for await (const chunk of request) {
    size += chunk.length;
    if (size > 32768) throw new ApiError(413, '요청 데이터가 너무 큽니다.');
    buffers.push(chunk);
  }
  try { return JSON.parse(Buffer.concat(buffers).toString('utf8')); }
  catch { throw new ApiError(400, 'JSON 형식이 올바르지 않습니다.'); }
}

/** Loopback-only broker for dedicated development runs; never attaches to existing processes. */
export async function createBroker(options = {}) {
  const renderingRoot = path.resolve(options.renderingRoot ?? defaultRendering);
  const repositoryRoot = await fs.realpath(options.repositoryRoot ?? path.resolve(renderingRoot, '..'));
  const runtimeRoot = path.resolve(options.runtimeRoot ?? path.join(renderingRoot, 'runtime'));
  const recordingsRoot = path.resolve(options.recordingsRoot ?? path.join(renderingRoot, 'recordings'));
  const localPython = path.join(renderingRoot, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
  const python = options.python ?? process.env.ATLAS_PYTHON ?? (existsSync(localPython) ? localPython : 'python');
  const manifest = JSON.parse(await fs.readFile(path.join(runtimeRoot, 'targets.json'), 'utf8'));
  const targets = new Map(manifest.targets.map(target => [target.id, target]));
  const allowedFiles = new Set(manifest.targets.flatMap(target => target.files.map(file => typeof file === 'string' ? file : file.fileId)));
  const sources = new Map();
  const runs = new Map();
  let closed = false;
  await fs.mkdir(recordingsRoot, { recursive: true });

  async function source(fileId) {
    if (!allowedFiles.has(fileId)) throw new ApiError(403, '실행 대상에 포함된 원본 파일만 열 수 있습니다.');
    const resolved = await fs.realpath(path.resolve(repositoryRoot, fileId));
    const relative = path.relative(repositoryRoot, resolved);
    if (relative.startsWith('..') || path.isAbsolute(relative)) throw new ApiError(403, '저장소 밖의 파일은 열 수 없습니다.');
    const bytes = await fs.readFile(resolved);
    const sourceHash = crypto.createHash('sha256').update(bytes).digest('hex');
    const cached = sources.get(fileId);
    if (cached?.sourceHash === sourceHash) return cached;
    const text = bytes.toString('utf8').replace(/^\uFEFF/, '');
    const lines = text.split(/\r?\n/);
    // DAP verifies exact breakability. These are candidates, not claimed executable statements.
    const executableLines = lines.flatMap((line, index) => line.trim() && !line.trim().startsWith('#') ? [index + 1] : []);
    const result = { fileId, lines, sourceHash, executableLines, breakpointCandidates: true, absolutePath: resolved };
    sources.set(fileId, result);
    return result;
  }

  function fileIdFor(filename) {
    if (!filename) return null;
    const relative = path.relative(repositoryRoot, path.resolve(filename)).replaceAll('\\', '/');
    return allowedFiles.has(relative) ? relative : null;
  }

  async function persist(run) {
    clearTimeout(run.persistTimer);
    run.persistTimer = null;
    const data = JSON.stringify(recordedRun(run));
    // Serialize writes so an earlier state cannot overwrite a later one.
    run.persistChain = (run.persistChain ?? Promise.resolve()).catch(() => {}).then(async () => {
      const temporary = path.join(recordingsRoot, `${run.id}.json.tmp`);
      await fs.writeFile(temporary, data, 'utf8');
      await fs.rename(temporary, path.join(recordingsRoot, `${run.id}.json`));
    });
    return run.persistChain;
  }

  function queuePersist(run) {
    if (run.persistTimer) return;
    run.persistTimer = setTimeout(() => { run.persistTimer = null; persist(run).catch(() => {}); }, 120);
  }

  function event(run, body) {
    if (run.events.length >= 12000) { run.truncated = true; return; }
    const copied = safeCopy(body);
    run.events.push({
      ...copied,
      seq: run.events.length + 1, runId: run.id,
      requestId: copied.requestId ?? run.id,
      processId: copied.processId ?? run.child?.pid ?? 0,
      threadId: copied.threadId ?? 0,
      timeMs: copied.timeMs ?? Math.round(performance.now() - run.startTime),
    });
    queuePersist(run);
  }

  async function validateBreakpoints(target, breakpoints) {
    if (!Array.isArray(breakpoints) || breakpoints.length > 80) throw new ApiError(400, 'Breakpoint 목록이 올바르지 않습니다.');
    const result = [];
    for (const breakpoint of breakpoints) {
      if (!target.files.includes(breakpoint.fileId)) throw new ApiError(400, '이 실행 대상 밖의 breakpoint는 사용할 수 없습니다.');
      const file = await source(breakpoint.fileId);
      if (!Number.isInteger(breakpoint.line) || breakpoint.line < 1 || breakpoint.line > file.lines.length) throw new ApiError(400, 'Breakpoint 줄 번호가 올바르지 않습니다.');
      if (!result.some(item => item.fileId === breakpoint.fileId && item.line === breakpoint.line)) result.push({ fileId: breakpoint.fileId, line: breakpoint.line });
    }
    return result;
  }

  async function applyBreakpoints(run, breakpoints) {
    const target = targets.get(run.target);
    const validated = await validateBreakpoints(target, breakpoints);
    const result = [];
    for (const fileId of target.files) {
      const file = await source(fileId);
      const requested = validated.filter(item => item.fileId === fileId);
      const response = await run.dap.request('setBreakpoints', {
        source: { path: file.absolutePath },
        breakpoints: requested.map(({ line }) => ({ line })), sourceModified: false,
      });
      response.breakpoints?.forEach((breakpoint, index) => result.push({
        fileId, requestedLine: requested[index]?.line,
        line: breakpoint.line ?? requested[index]?.line,
        verified: Boolean(breakpoint.verified), message: safeText(breakpoint.message ?? '', 300), id: breakpoint.id,
      }));
    }
    run.breakpoints = result;
    queuePersist(run);
    return result;
  }

  async function snapshotReference(run, reference, type = 'dict', depth = 0, budget = { remaining: 180 }, indexedCount) {
    const array = ['list', 'tuple'].includes(type);
    if (depth > 7 || budget.remaining <= 0) return array ? ['[수집 범위 제한]'] : '[수집 범위 제한]';
    const response = await run.dap.request('variables', {
      variablesReference: reference, start: 0, count: 48, ...(array ? { filter: 'indexed' } : {}),
    });
    const children = (response.variables ?? []).filter(variable => !variable.name.startsWith('__') && !['special variables', 'function variables', 'len()'].includes(variable.name));
    const result = array ? [] : {};
    let budgetTruncated = false;
    for (const variable of children.slice(0, 48)) {
      if (--budget.remaining < 0) {
        if (array) result.push('[변수 개수 제한]'); else result['…'] = '[변수 개수 제한]';
        budgetTruncated = true;
        break;
      }
      const name = plainVariableName(variable.name);
      let value;
      if (secretName.test(name)) value = '[마스킹]';
      else if (variable.type === 'bytes') value = dapValue(variable);
      else if (variable.variablesReference > 0 && pythonTypes.has(variable.type)) value = await snapshotReference(run, variable.variablesReference, variable.type, depth + 1, budget, variable.indexedVariables);
      else if (variable.variablesReference > 0) value = `[${safeText(variable.type ?? '객체', 100)} · 내부 생략]`;
      else value = dapValue(variable);
      if (array) result.push(value); else result[name] = value;
    }
    if (array && !budgetTruncated) {
      const total = Number.isInteger(indexedCount) ? indexedCount : children.length;
      if (total > result.length) result.push({ omittedItems: total - result.length });
    }
    return result;
  }

  async function frameVariables(run, frameId) {
    const response = await run.dap.request('scopes', { frameId });
    const scopes = response.scopes ?? [];
    const locals = scopes.find(scope => /locals|지역/i.test(scope.name)) ?? scopes.find(scope => !scope.expensive);
    if (!locals) return { variables: [], snapshot: {} };
    const raw = await run.dap.request('variables', { variablesReference: locals.variablesReference, start: 0, count: 96 });
    const allow = targets.get(run.target).variables;
    const useful = (raw.variables ?? []).filter(variable => !variable.name.startsWith('__') && (!allow?.length || allow.includes(variable.name)));
    const snapshot = {};
    for (const variable of useful.slice(0, 40)) {
      if (secretName.test(variable.name)) snapshot[variable.name] = '[마스킹]';
      else if (variable.type === 'bytes') snapshot[variable.name] = dapValue(variable);
      else if (variable.variablesReference > 0 && pythonTypes.has(variable.type)) snapshot[variable.name] = await snapshotReference(run, variable.variablesReference, variable.type, 0, { remaining: 180 }, variable.indexedVariables);
      else if (variable.variablesReference > 0) snapshot[variable.name] = `[${safeText(variable.type ?? '객체', 100)} · 내부 생략]`;
      else snapshot[variable.name] = dapValue(variable);
    }
    return { variables: useful.map(sanitizeDapVariable), snapshot };
  }

  async function collectStop(run, body) {
    if (terminalStatuses.has(run.status)) return;
    run.stopGeneration = (run.stopGeneration ?? 0) + 1;
    const generation = run.stopGeneration;
    const response = await run.dap.request('stackTrace', { threadId: body.threadId, startFrame: 0, levels: 40 });
    const allFrames = response.stackFrames ?? [];
    const frames = allFrames.map(frame => ({ id: frame.id, fileId: fileIdFor(frame.source?.path), line: frame.line, name: safeText(frame.name, 180) })).filter(frame => frame.fileId);
    // An allowed ancestor is a caller, not the current position. Step In can enter a fixture.
    const actualTop = allFrames[0];
    const currentFileId = fileIdFor(actualTop?.source?.path);
    const current = currentFileId ? frames.find(frame => frame.id === actualTop.id) : null;
    const outside = !current;
    const variables = current ? await frameVariables(run, current.id) : { variables: [], snapshot: {} };
    if (generation !== run.stopGeneration || terminalStatuses.has(run.status)) return;
    const original = current ? await source(current.fileId) : null;
    if (generation !== run.stopGeneration || terminalStatuses.has(run.status)) return;
    run.status = 'paused';
    run.stop = { threadId: body.threadId, reason: body.reason, description: safeText(body.description ?? '', 300),
      outside, current: current ? { fileId: current.fileId, line: current.line, name: current.name } : null,
      frames, frameId: current?.id, variables: variables.variables };
    event(run, { kind: 'stop', source: 'dap', fileId: current?.fileId ?? null, line: current?.line ?? null,
        function: current?.name ?? safeText(actualTop?.name ?? '대상 범위 밖', 180), sourceHash: original?.sourceHash ?? null, threadId: body.threadId,
        stack: frames.toReversed().map(frame => ({ fileId: frame.fileId, line: frame.line, function: frame.name })),
        variables: variables.snapshot, detail: { reason: body.reason, outside,
          position: outside ? '실제 현재 프레임은 원본 대상 범위 밖' : '실행 전 현재 줄', truncatedFrames: allFrames.length !== frames.length },
      });
    await persist(run);
  }

  function stopRun(run, status = 'stopped') {
    if (run.cleanupPromise) return run.cleanupPromise;
    if (terminalStatuses.has(run.status)) return Promise.resolve();
    run.cleanupPromise = cleanupRun(run, status);
    return run.cleanupPromise;
  }

  async function cleanupRun(run, status) {
    run.status = status;
    run.stopGeneration = (run.stopGeneration ?? 0) + 1;
    run.stop = undefined;
    clearTimeout(run.lifetimeTimer);
    run.abort?.abort();
    if (run.dap) {
      await run.dap.request('disconnect', { terminateDebuggee: true }, 1800).catch(() => {});
      run.dap.close();
    }
    if (run.child?.pid && run.child.exitCode === null) {
      if (process.platform === 'win32') {
        await new Promise(resolve => { const killer = spawn('taskkill', ['/PID', String(run.child.pid), '/T', '/F'], { windowsHide: true, stdio: 'ignore' }); killer.once('error', resolve); killer.once('exit', resolve); });
      } else run.child.kill('SIGKILL');
    }
    await persist(run);
  }

  async function configureDebug(run, port) {
    run.dap = await DapClient.connect(port, { signal: run.abort.signal, timeout: 20000 });
    run.dap.on('stopped', body => {
      run.stopChain = (run.stopChain ?? Promise.resolve()).then(() => collectStop(run, body)).catch(error => {
        if (!terminalStatuses.has(run.status)) { run.error = safeText(error.message, 500); stopRun(run, 'failed').catch(() => {}); }
      });
    });
    run.dap.on('continued', () => {
      if (!terminalStatuses.has(run.status)) { run.status = 'running'; run.stop = undefined; queuePersist(run); }
    });
    run.dap.on('breakpoint', body => {
      const breakpoint = body.breakpoint;
      if (!breakpoint) return;
      const found = run.breakpoints?.find(item => item.id === breakpoint.id);
      if (found) { found.verified = breakpoint.verified; found.line = breakpoint.line ?? found.line; found.message = safeText(breakpoint.message ?? '', 300); queuePersist(run); }
    });
    run.dap.on('terminated', () => { run.dap.close(); });
    await run.dap.request('initialize', {
      clientID: 'repository-atlas', clientName: 'Repository Atlas', adapterID: 'python',
      pathFormat: 'path', linesStartAt1: true, columnsStartAt1: true,
      supportsVariableType: true, supportsVariablePaging: true, supportsRunInTerminalRequest: false,
    });
    const attached = run.dap.request('attach', { justMyCode: false, redirectOutput: false, subProcess: false }, 30000);
    // Attach completes only after configurationDone, so its promise must remain pending here.
    attached.catch(() => {});
    await run.dap.waitFor('initialized');
    await applyBreakpoints(run, run.requestedBreakpoints);
    await run.dap.request('setExceptionBreakpoints', { filters: ['uncaught'] });
    if (run.status === 'starting') run.status = 'running';
    await run.dap.request('configurationDone');
    await attached;
  }

  async function startRun(body) {
    if (closed) throw new ApiError(503, '서버가 종료 중입니다.');
    const target = targets.get(body.target);
    if (!target || !['debug', 'record'].includes(body.mode)) throw new ApiError(400, '실행 모드 또는 대상이 올바르지 않습니다.');
    if (typeof body.query !== 'string' || !body.query.trim() || body.query.length > 4000 || body.query.includes('\0')) throw new ApiError(400, 'Query는 1~4000자의 문자열이어야 합니다.');
    if ([...runs.values()].filter(run => !terminalStatuses.has(run.status)).length >= 2) throw new ApiError(409, '동시에 최대 두 실행만 열 수 있습니다. 먼저 기존 실행을 종료하세요.');
    const requestedBreakpoints = await validateBreakpoints(target, body.breakpoints ?? target.defaultBreakpoints ?? []);
    const id = crypto.randomUUID();
    const sourceHashes = {};
    for (const fileId of target.files) sourceHashes[fileId] = (await source(fileId)).sourceHash;
    const run = { id, target: target.id, mode: body.mode, query: safeText(body.query, 8192), createdAt: new Date().toISOString(),
      status: 'starting', events: [], requestedBreakpoints, breakpoints: requestedBreakpoints.map(bp => ({ ...bp, verified: false })),
      sourceHashes, startTime: performance.now(), abort: new AbortController(),
      provenance: { label: manifest.provenanceLabel, description: manifest.description, assumptions: target.assumptions },
    };
    runs.set(id, run);
    const port = body.mode === 'debug' ? await freePort() : null;
    const runnerArguments = [path.join(runtimeRoot, 'runner.py'), '--target', target.id, `--query=${body.query}`, '--mode', body.mode, '--run-id', id];
    const args = body.mode === 'debug' ? ['-u', '-m', 'debugpy', '--listen', `127.0.0.1:${port}`, '--wait-for-client', ...runnerArguments] : ['-u', ...runnerArguments];
    // Do not inherit application credentials into the dedicated process.
    const inherited = ['SYSTEMROOT', 'SystemRoot', 'WINDIR', 'PATH', 'Path', 'PATHEXT', 'TEMP', 'TMP', 'USERPROFILE', 'HOME', 'LANG'];
    const environment = Object.fromEntries(inherited.filter(key => process.env[key]).map(key => [key, process.env[key]]));
    Object.assign(environment, { PYTHONUTF8: '1', PYTHONUNBUFFERED: '1', PYTHONDONTWRITEBYTECODE: '1', ATLAS_REPOSITORY_ROOT: repositoryRoot });
    run.child = spawn(python, args, { cwd: repositoryRoot, env: environment, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
    run.status = body.mode === 'debug' ? 'starting' : 'running';
    let stdout = '';
    let stderr = '';
    run.child.stdout.setEncoding('utf8');
    run.child.stderr.setEncoding('utf8');
    const consumeLine = line => {
      if (line.startsWith('ATLAS_EVENT ')) {
        try { event(run, JSON.parse(line.slice(12))); } catch { /* Ignore unrelated application output. */ }
      } else if (line.startsWith('ATLAS_RESULT ')) {
        try { run.result = safeCopy(JSON.parse(line.slice(13))); queuePersist(run); } catch { /* A malformed result cannot execute anything. */ }
      }
    };
    run.child.stdout.on('data', chunk => {
      stdout += chunk;
      let separator;
      while ((separator = stdout.indexOf('\n')) >= 0) { consumeLine(stdout.slice(0, separator).replace(/\r$/, '')); stdout = stdout.slice(separator + 1); }
      if (stdout.length > 2 * 1024 * 1024) stdout = '';
    });
    run.child.stderr.on('data', chunk => { stderr = (stderr + chunk).slice(-6000); });
    run.child.once('error', error => { run.error = safeText(`Python 실행 실패: ${error.message}`, 600); stopRun(run, 'failed').catch(() => {}); });
    run.child.once('exit', (code, signal) => {
      if (stdout.trim()) consumeLine(stdout.trim());
      clearTimeout(run.lifetimeTimer);
      if (!terminalStatuses.has(run.status)) {
        run.status = code === 0 ? 'completed' : 'failed';
        if (code !== 0) run.error = safeText(stderr || `실행 종료: ${code ?? signal}`, 1500);
      }
      run.stopGeneration = (run.stopGeneration ?? 0) + 1;
      run.stop = undefined;
      run.abort.abort();
      run.dap?.close();
      persist(run).catch(() => {});
    });
    run.lifetimeTimer = setTimeout(() => { run.error = '전용 개발 실행의 15분 제한에 도달했습니다.'; stopRun(run, 'stopped').catch(() => {}); }, options.lifetimeMs ?? 15 * 60 * 1000);
    if (body.mode === 'debug') configureDebug(run, port).catch(error => {
      if (!terminalStatuses.has(run.status)) { run.error = safeText(error.message, 900); stopRun(run, 'failed').catch(() => {}); }
    });
    await persist(run);
    return publicRun(run);
  }

  async function getRun(id) {
    if (!/^[a-f0-9-]{36}$/.test(id)) throw new ApiError(404, '실행 기록을 찾지 못했습니다.');
    if (runs.has(id)) return runs.get(id);
    try {
      const saved = JSON.parse(await fs.readFile(path.join(recordingsRoot, `${id}.json`), 'utf8'));
      if (!terminalStatuses.has(saved.status)) { saved.status = 'stopped'; saved.error = '서버 재시작으로 종료된 실행입니다.'; saved.stop = undefined; saved.paused = false; }
      return saved;
    } catch { throw new ApiError(404, '실행 기록을 찾지 못했습니다.'); }
  }

  async function listRuns() {
    const all = new Map([...runs.values()].map(run => [run.id, publicRun(run)]));
    const files = await fs.readdir(recordingsRoot);
    for (const filename of files.filter(name => /^[a-f0-9-]{36}\.json$/.test(name)).slice(-100)) {
      const id = filename.slice(0, -5);
      if (all.has(id)) continue;
      try { all.set(id, await getRun(id)); } catch { /* Ignore incomplete or damaged records. */ }
    }
    return [...all.values()].sort((a, b) => b.createdAt.localeCompare(a.createdAt)).slice(0, 100).map(run => ({ id: run.id, target: run.target, mode: run.mode, status: run.status, query: run.query, createdAt: run.createdAt, eventCount: run.events?.length ?? 0 }));
  }

  async function availability() {
    const result = await new Promise(resolve => {
      const child = spawn(python, ['-c', 'import debugpy; print(debugpy.__version__)'], { windowsHide: true, stdio: ['ignore', 'pipe', 'ignore'] });
      let output = '';
      const timer = setTimeout(() => { child.kill(); resolve({ debugpy: false }); }, 5000);
      child.stdout.on('data', chunk => { output += chunk; });
      child.once('error', () => { clearTimeout(timer); resolve({ debugpy: false }); });
      child.once('exit', code => { clearTimeout(timer); resolve({ debugpy: code === 0, debugpyVersion: code === 0 ? output.trim() : undefined }); });
    });
    return { available: true, ...result, targets: targets.size, provenanceLabel: manifest.provenanceLabel, description: manifest.description };
  }

  const server = http.createServer(async (request, response) => {
    const send = (status, body) => {
      response.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff' });
      response.end(JSON.stringify(body));
    };
    try {
      if (!isLoopbackRequest(request)) throw new ApiError(403, '로컬 Repository Atlas 화면에서만 접근할 수 있습니다.');
      const url = new URL(request.url, 'http://127.0.0.1');
      if (!url.pathname.startsWith('/atlas-api/')) throw new ApiError(404, 'API 경로를 찾지 못했습니다.');
      const route = url.pathname.slice('/atlas-api'.length);
      if (request.method === 'GET' && route === '/status') return send(200, await availability());
      if (request.method === 'GET' && route === '/targets') return send(200, manifest);
      if (request.method === 'GET' && route === '/source') { const { absolutePath, ...file } = await source(url.searchParams.get('fileId')); return send(200, file); }
      if (request.method === 'GET' && route === '/runs') return send(200, { runs: await listRuns() });
      if (request.method === 'POST' && route === '/runs') return send(201, await startRun(await readJsonBody(request)));
      const match = /^\/runs\/([a-f0-9-]{36})(?:\/(action|breakpoints|variables))?$/.exec(route);
      if (!match) throw new ApiError(404, 'API 경로를 찾지 못했습니다.');
      const run = await getRun(match[1]);
      if (request.method === 'GET' && !match[2]) return send(200, publicRun(run));
      if (request.method === 'POST' && match[2] === 'breakpoints') {
        if (run.mode !== 'debug' || !run.dap || terminalStatuses.has(run.status)) throw new ApiError(409, '연결된 디버깅 실행이 필요합니다.');
        const body = await readJsonBody(request);
        return send(200, { breakpoints: await applyBreakpoints(run, body.breakpoints) });
      }
      if (request.method === 'GET' && match[2] === 'variables') {
        if (run.status !== 'paused' || !run.dap) throw new ApiError(409, '실행이 중단된 동안에만 현재 변수를 볼 수 있습니다.');
        const reference = Number(url.searchParams.get('variablesReference'));
        if (reference > 0 && Number.isSafeInteger(reference)) {
          const variables = await run.dap.request('variables', { variablesReference: reference, start: 0, count: 64 });
          return send(200, { variables: (variables.variables ?? []).filter(variable => !['special variables', 'function variables', 'len()'].includes(variable.name)).map(sanitizeDapVariable) });
        }
        const frameId = Number(url.searchParams.get('frameId') ?? run.stop?.frameId);
        if (!run.stop?.frames.some(frame => frame.id === frameId)) throw new ApiError(400, '현재 호출 스택의 frame을 선택하세요.');
        const result = await frameVariables(run, frameId);
        return send(200, { ...result, frameId });
      }
      if (request.method === 'POST' && match[2] === 'action') {
        const { action } = await readJsonBody(request);
        if (action === 'stop') { await stopRun(run); return send(200, publicRun(run)); }
        if (!run.dap || run.mode !== 'debug' || terminalStatuses.has(run.status)) throw new ApiError(409, '연결된 디버깅 실행이 필요합니다.');
        if (action === 'pause') {
          const { threads } = await run.dap.request('threads');
          const threadId = threads?.[0]?.id;
          if (!threadId) throw new ApiError(409, '중단할 실행 스레드가 없습니다.');
          await run.dap.request('pause', { threadId });
        } else {
          if (!['continue', 'next', 'stepIn', 'stepOut'].includes(action)) throw new ApiError(400, '지원하지 않는 디버거 동작입니다.');
          if (run.status !== 'paused' || !run.stop) throw new ApiError(409, '실행이 중단된 다음 사용할 수 있습니다.');
          const threadId = run.stop.threadId;
          run.status = 'running'; run.stop = undefined; run.stopGeneration = (run.stopGeneration ?? 0) + 1;
          await run.dap.request(action, { threadId });
        }
        await persist(run);
        return send(200, publicRun(run));
      }
      throw new ApiError(405, '지원하지 않는 요청입니다.');
    } catch (error) { send(error.status ?? 500, { error: safeText(error.message ?? '로컬 실행 제어 오류', 900) }); }
  });

  return {
    server, repositoryRoot, python,
    async listen(port = options.port ?? 4174) {
      await new Promise((resolve, reject) => { server.once('error', reject); server.listen(port, '127.0.0.1', resolve); });
      return server.address();
    },
    async close() {
      closed = true;
      // A concurrent Stop marks terminal status before its transport/process cleanup finishes.
      await Promise.all([...runs.values()].map(run => run.cleanupPromise ?? stopRun(run)));
      await Promise.all([...runs.values()].map(run => run.persistChain?.catch(() => {})));
      if (server.listening) await new Promise(resolve => server.close(resolve));
    },
  };
}

import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { existsSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { createBroker } from '../server/broker.js';
import { DapClient } from '../server/dap.js';
import { safeCopy, safeText, sanitizeDapVariable, isLoopbackRequest, dapValue } from '../server/safety.js';

const rendering = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const venv = path.join(rendering, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
const python = process.env.ATLAS_PYTHON ?? (existsSync(venv) ? venv : 'python');
const hasDebugpy = spawnSync(python, ['-c', 'import debugpy'], { windowsHide: true, stdio: 'ignore' }).status === 0;

async function fixture({ withHelper = false } = {}) {
  const repositoryRoot = await fs.mkdtemp(path.join(os.tmpdir(), 'atlas-broker-'));
  const runtimeRoot = path.join(repositoryRoot, 'runtime');
  const recordingsRoot = path.join(repositoryRoot, 'recordings');
  await fs.mkdir(runtimeRoot);
  await fs.writeFile(path.join(repositoryRoot, 'example.py'), [
    'def execute(query):',
    '    value = query.strip()',
    '    payload = {"query": value, "Authorization": "Bearer fixture-secret", "image": "data:image/png;base64,AAAABBBB", "messages": [{"role": "user", "content": [{"type": "text", "text": value}, {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAABBBB"}}]}], "sequence": list(range(61)), "pair": ("first", "second")}',
    withHelper ? '    step = count(value)' : '    step = len(value)',
    '    step += 1',
    '    return {"answer": value, "step": step}',
    '',
  ].join('\n'));
  await fs.writeFile(path.join(runtimeRoot, 'targets.json'), JSON.stringify({ targets: [{ id: 'fixture', label: 'Fixture', files: ['example.py'], variables: ['query', 'value', 'payload', 'step'], defaultBreakpoints: [{ fileId: 'example.py', line: 4 }] }] }));
  await fs.writeFile(path.join(runtimeRoot, 'runner.py'), [
    'import argparse, json, runpy, os',
    'def count(value):',
    '    return len(value)',
    'p = argparse.ArgumentParser()',
    'for name in ("target", "query", "mode", "run-id"): p.add_argument("--" + name, required=True)',
    'args = p.parse_args()',
    'namespace = runpy.run_path(os.path.join(os.environ["ATLAS_REPOSITORY_ROOT"], "example.py"))',
    'namespace["execute"].__globals__["count"] = count',
    'result = namespace["execute"](args.query)',
    'print("ATLAS_RESULT " + json.dumps(result), flush=True)',
  ].join('\n'));
  const broker = await createBroker({ repositoryRoot, runtimeRoot, recordingsRoot, renderingRoot: rendering, python, lifetimeMs: 30000 });
  const address = await broker.listen(0);
  const url = `http://127.0.0.1:${address.port}/atlas-api`;
  const api = async (route, body) => {
    const response = await fetch(url + route, body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    return { status: response.status, body: await response.json() };
  };
  return { broker, repositoryRoot, api, url, async close() { await broker.close(); await fs.rm(repositoryRoot, { recursive: true, force: true }); } };
}

async function until(api, id, predicate, timeout = 22000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    const run = (await api(`/runs/${id}`)).body;
    if (predicate(run)) return run;
    if (run.status === 'failed') throw new Error(run.error);
    await new Promise(resolve => setTimeout(resolve, 80));
  }
  throw new Error('Run state wait timed out');
}

test('snapshots remove credentials, base64 images, and unsafe DAP container repr', () => {
  const copied = safeCopy({ Authorization: 'Bearer private', api_key: 'private', image: 'data:image/png;base64,AAAABBBB', body: { query: 'normal question' } });
  assert.equal(copied.Authorization, '[마스킹]');
  assert.equal(copied.api_key, '[마스킹]');
  assert.equal(copied.body.query, 'normal question');
  assert.ok(!JSON.stringify(copied).includes('AAAABBBB'));
  assert.ok(!safeText('Bearer fixture-secret').includes('fixture-secret'));
  assert.ok(!sanitizeDapVariable({ name: 'payload', type: 'dict', variablesReference: 5, value: "{'Authorization': 'private'}" }).value.includes('private'));
  assert.equal(sanitizeDapVariable({ name: 'api_key', value: 'private', variablesReference: 0 }).value, '[마스킹]');
  const chunk = dapValue({ type: 'bytes', value: "b'data: {\"answer\": \"ok\"}\\n\\n'", variablesReference: 12 });
  assert.equal(chunk.type, 'bytes');
  assert.equal(chunk.preview, 'data: {"answer": "ok"}\n\n');
  assert.equal(chunk.truncated, false);
  assert.equal(sanitizeDapVariable({ name: 'chunk', type: 'bytes', value: "b'data: ok\\n'", variablesReference: 12 }).value, 'data: ok\n');
  assert.ok(!dapValue({ type: 'bytes', value: "b'Bearer fixture-secret'" }).preview.includes('fixture-secret'));
});

test('broker requires local Host and Origin', () => {
  const request = { socket: { remoteAddress: '127.0.0.1' }, headers: { host: '127.0.0.1:4174', origin: 'http://127.0.0.1:4173' } };
  assert.equal(isLoopbackRequest(request), true);
  assert.equal(isLoopbackRequest({ ...request, headers: { ...request.headers, origin: 'https://example.com' } }), false);
  assert.equal(isLoopbackRequest({ ...request, headers: { host: 'evil.example:4174' } }), false);
});

test('source whitelist, invalid inputs, dedicated record, and offline persistence', { timeout: 15000 }, async () => {
  const app = await fixture();
  try {
    const source = await app.api('/source?fileId=example.py');
    assert.equal(source.status, 200);
    assert.equal(source.body.lines[0], 'def execute(query):');
    assert.match(source.body.sourceHash, /^[a-f0-9]{64}$/);
    assert.equal((await app.api('/source?fileId=../secrets.env')).status, 403);
    assert.equal((await app.api('/runs', { target: 'unknown', mode: 'record', query: 'x' })).status, 400);
    assert.equal((await app.api('/runs', { target: 'fixture', mode: 'record', query: 'x', breakpoints: [{ fileId: 'example.py', line: -1 }] })).status, 400);
    const created = await app.api('/runs', { target: 'fixture', mode: 'record', query: '--flag-like query', breakpoints: [] });
    assert.equal(created.status, 201);
    const completed = await until(app.api, created.body.id, run => run.status === 'completed');
    assert.deepEqual(completed.result, { answer: '--flag-like query', step: 18 });
    const files = await fs.readdir(path.join(app.repositoryRoot, 'recordings'));
    assert.ok(files.includes(`${completed.id}.json`));
    assert.equal((await app.api(`/runs/${completed.id}/variables?frameId=0`)).status, 409);
    assert.equal((await app.api('/runs')).body.runs[0].id, completed.id);
  } finally { await app.close(); }
});

test('real debugpy breakpoint stops original code, inspects locals, steps, and continues', { timeout: 45000, skip: !hasDebugpy && 'Run npm run setup:debug to install scoped debugpy' }, async () => {
  const app = await fixture();
  try {
    const created = await app.api('/runs', { target: 'fixture', mode: 'debug', query: ' hello ' });
    assert.equal(created.status, 201);
    const id = created.body.id;
    const first = await until(app.api, id, run => run.status === 'paused' && run.stop?.frames.length);
    assert.equal(first.stop.frames[0].fileId, 'example.py');
    assert.equal(first.stop.frames[0].line, 4);
    assert.equal(first.events.at(-1).source, 'dap');
    assert.equal(first.events.at(-1).variables.value, 'hello');
    const payload = first.events.at(-1).variables.payload;
    assert.ok(Array.isArray(payload.messages));
    assert.ok(Array.isArray(payload.messages[0].content));
    assert.equal(payload.messages[0].content[0].text, 'hello');
    assert.equal(payload.messages[0].content[1].image_url.url.redacted, true);
    assert.deepEqual(payload.pair, ['first', 'second']);
    assert.ok(Array.isArray(payload.sequence));
    assert.equal(payload.sequence[0], 0);
    assert.ok(payload.sequence.length <= 49);
    assert.equal(payload.sequence.at(-1).omittedItems, 13);
    const serialized = JSON.stringify(first);
    assert.ok(!serialized.includes('fixture-secret'));
    assert.ok(!serialized.includes('AAAABBBB'));
    const variables = (await app.api(`/runs/${id}/variables?frameId=${first.stop.frameId}`)).body;
    assert.ok(variables.variables.some(variable => variable.name === 'payload'));
    await app.api(`/runs/${id}/action`, { action: 'next' });
    const next = await until(app.api, id, run => run.status === 'paused' && run.stop?.frames[0].line === 5);
    assert.equal(next.events.at(-1).variables.step, 5);
    await app.api(`/runs/${id}/action`, { action: 'continue' });
    const completed = await until(app.api, id, run => run.status === 'completed');
    assert.deepEqual(completed.result, { answer: 'hello', step: 6 });
    assert.equal(completed.paused, false);
    assert.equal(completed.events.filter(event => event.kind === 'stop').length, 2);
  } finally { await app.close(); }
});

test('Step In to an untracked helper preserves callers without claiming their line is current', { timeout: 45000, skip: !hasDebugpy && 'Scoped debugpy is required' }, async () => {
  const app = await fixture({ withHelper: true });
  try {
    const created = await app.api('/runs', { target: 'fixture', mode: 'debug', query: 'hello' });
    const id = created.body.id;
    await until(app.api, id, run => run.status === 'paused' && run.stop?.current?.line === 4);
    await app.api(`/runs/${id}/action`, { action: 'stepIn' });
    const outside = await until(app.api, id, run => run.status === 'paused' && run.stop?.outside === true);
    assert.equal(outside.stop.current, null);
    assert.equal(outside.stop.frameId, undefined);
    assert.equal(outside.stop.frames[0].fileId, 'example.py');
    assert.equal(outside.stop.frames[0].line, 4);
    const stopped = outside.events.at(-1);
    assert.equal(stopped.fileId, null);
    assert.equal(stopped.line, null);
    assert.equal(stopped.function, 'count');
    assert.deepEqual(stopped.variables, {});
    assert.equal(stopped.stack.at(-1).fileId, 'example.py');
    const ancestor = await app.api(`/runs/${id}/variables?frameId=${outside.stop.frames[0].id}`);
    assert.equal(ancestor.body.snapshot.value, 'hello');
    await app.api(`/runs/${id}/action`, { action: 'stepOut' });
    const returned = await until(app.api, id, run => run.status === 'paused' && run.stop?.outside === false);
    assert.equal(returned.stop.current.fileId, 'example.py');
    await app.api(`/runs/${id}/action`, { action: 'stop' });
  } finally { await app.close(); }
});

test('broker shutdown waits for an already pending Stop cleanup', { timeout: 45000, skip: !hasDebugpy && 'Scoped debugpy is required' }, async () => {
  const app = await fixture();
  const originalRequest = DapClient.prototype.request;
  let releaseDisconnect;
  let disconnectEntered;
  const release = new Promise(resolve => { releaseDisconnect = resolve; });
  const entered = new Promise(resolve => { disconnectEntered = resolve; });
  let stopRequest;
  try {
    const created = await app.api('/runs', { target: 'fixture', mode: 'debug', query: 'hello' });
    const id = created.body.id;
    await until(app.api, id, run => run.status === 'paused');
    DapClient.prototype.request = function (command, ...args) {
      if (command === 'disconnect') {
        disconnectEntered();
        return release.then(() => originalRequest.call(this, command, ...args));
      }
      return originalRequest.call(this, command, ...args);
    };
    stopRequest = app.api(`/runs/${id}/action`, { action: 'stop' });
    await entered;
    assert.equal((await app.api(`/runs/${id}`)).body.status, 'stopped');
    let closed = false;
    const closing = app.broker.close().then(() => { closed = true; });
    await new Promise(resolve => setTimeout(resolve, 60));
    assert.equal(closed, false, 'shutdown must await cleanup despite terminal status');
    releaseDisconnect();
    const stopped = await stopRequest;
    assert.equal(stopped.body.status, 'stopped');
    await closing;
    assert.equal(closed, true);
  } finally {
    releaseDisconnect();
    DapClient.prototype.request = originalRequest;
    await stopRequest?.catch(() => {});
    await app.close();
  }
});

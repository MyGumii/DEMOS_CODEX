import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createScenarioIndex, examplePayload } from '../src/scenarios.js';

const base = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const root = path.dirname(base);
const json = name => JSON.parse(fs.readFileSync(path.join(base, 'public', name), 'utf8'));
const data = json('repo-map.json');
const packs = [json('scenarios-query.json'), json('scenarios-api.json')];
const index = createScenarioIndex(data, packs);
const inventory = new Map(data.files.map(file => [file.id, file]));

test('all four authored examples contain complete data transformations and valid current source evidence', () => {
  assert.equal(index.size, 4);
  assert.equal([...index.values()].reduce((total, example) => total + example.steps.length, 0), 26);
  for (const example of index.values()) {
    assert.equal(example.kind, 'example');
    assert.ok(example.label && example.input.query && Array.isArray(example.input.attachments));
    assert.ok(example.result.description && example.result.payload);
    for (const step of example.steps) {
      for (const field of ['title', 'description', 'input', 'processing', 'output']) assert.ok(step[field]?.trim(), `${example.id}: ${field}`);
      assert.ok(step.line > 0 && step.line <= inventory.get(step.fileId).lines, example.id);
      assert.ok(step.payload && step.evidence.length, example.id);
      for (const evidence of step.evidence) {
        assert.ok(inventory.has(evidence.path), `${example.id}: ${evidence.path}`);
        const lines = fs.readFileSync(path.join(root, evidence.path), 'utf8').split(/\r?\n/);
        assert.ok(Number.isInteger(evidence.line) && evidence.line > 0 && evidence.line <= lines.length, `${evidence.path}:${evidence.line}`);
        assert.ok(lines[evidence.line - 1].trim() && evidence.note, `${example.id}: empty source evidence`);
      }
    }
  }
});

test('image comparison keeps the same query and shows multimodal content preserved through API messages', () => {
  const plain = index.get('query-text-only'), image = index.get('query-with-image');
  assert.equal(plain.input.query, image.input.query);
  assert.equal(plain.input.attachments.length, 0);
  assert.equal(image.input.attachments.length, 1);
  const apiBodies = example => example.steps.map(step => step.payload.request?.body).filter(body => body?.messages && body.stream === true);
  const textBody = apiBodies(plain)[0], imageBody = apiBodies(image)[0];
  assert.ok(textBody && imageBody, 'both examples must expose the API message body');
  assert.deepEqual(textBody.messages.map(message => message.role), ['system', 'system', 'user']);
  assert.deepEqual(imageBody.messages.map(message => message.role), ['system', 'system', 'user']);
  for (const example of [plain, image]) for (const step of example.steps) if (step.payload.request?.body) {
    assert.equal('illustrative' in step.payload.request.body, false);
    assert.equal('hasImages' in step.payload.request.body, false);
    assert.equal('actual_model_call' in step.payload.request.body, false);
  }
  assert.equal(typeof textBody.messages.find(message => message.role === 'user').content, 'string');
  const imageContent = imageBody.messages.find(message => message.role === 'user').content;
  assert.ok(imageContent.some(part => part.type === 'text' && part.text === image.input.query));
  assert.ok(imageContent.some(part => part.type === 'image_url' && part.image_url.url.startsWith('data:image/')));
  assert.equal(image.result.payload.ocr_used, false);
});

test('Hermes proxy example uses HTTP forwarding while web chat exposes the separate CLI and token transport', () => {
  const api = index.get('api-hermes-external'), web = index.get('api-hermes-web');
  assert.ok(api && web);
  assert.ok(api.steps.every(step => step.fileId === 'hermes-web/hermes_proxy.py'));
  assert.ok(examplePayload(api.steps).includes('/v1/chat/completions'));
  assert.ok(examplePayload(api.steps).includes('requests.request'));
  assert.ok(web.steps.some(step => step.fileId === 'hermes-web/server.py'));
  assert.ok(web.steps.some(step => step.fileId === 'hermes-web/static/index.html'));
  assert.ok(examplePayload(web.steps).includes('CLI'));
  assert.ok(examplePayload(web.steps).includes('token'));
});

test('a missing file removes the whole example so skipped stages cannot fabricate a new flow', () => {
  const changed = { ...data, files: data.files.filter(file => file.id !== 'code_assist_v1/static/app.js') };
  const refreshed = createScenarioIndex(changed, packs);
  assert.equal(refreshed.has('query-with-image'), false);
  assert.equal(refreshed.has('query-text-only'), true);
  assert.equal(refreshed.get('query-text-only').steps.length, index.get('query-text-only').steps.length);
  assert.equal(examplePayload({ message: '<script>example</script>' }), '{\n  "message": "<script>example</script>"\n}');
  assert.equal(examplePayload({ exampleMeta: { illustrative: true }, data: 'example' }), '{\n  "data": "example"\n}');
});

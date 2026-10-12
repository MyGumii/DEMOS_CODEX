import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createGuideIndex, toursForFile } from '../src/guides.js';

const rendering = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const root = path.dirname(rendering);
const json = name => JSON.parse(fs.readFileSync(path.join(rendering, 'public', name), 'utf8'));
const data = json('repo-map.json');
const packs = [json('guides-core.json'), json('guides-edge.json')];
const original = json('tour.json');
const index = createGuideIndex(data, packs, original);
const inventory = new Map(data.files.map(file => [file.id, file]));

function validEvidence(evidence, owner) {
  assert.ok(evidence?.length, `${owner}: missing source evidence`);
  for (const item of evidence) {
    assert.ok(inventory.has(item.path), `${owner}: evidence outside inventory ${item.path}`);
    const source = fs.readFileSync(path.join(root, item.path), 'utf8');
    assert.ok(Number.isInteger(item.line) && item.line > 0 && item.line <= source.split(/\r?\n/).length,
      `${owner}: invalid source line ${item.path}:${item.line}`);
    assert.ok(item.note?.trim(), `${owner}: empty evidence explanation`);
  }
}

test('every district has a substantive guide, major files and its own multi-step route', () => {
  assert.equal(index.districts.size, data.districts.length);
  assert.equal(index.tours.size, data.districts.length + 1);
  for (const district of data.districts) {
    const guide = index.districts.get(district.id);
    assert.ok(guide.purpose?.length > 30 && guide.flow?.length > 30, district.id);
    assert.ok(guide.keyFiles.length >= 3, district.id);
    assert.ok(guide.keyFiles.every(id => index.files.has(id)), `${district.id}: unexplained major file`);
    const route = index.tours.get(guide.tourId);
    assert.equal(route.districtId, district.id);
    assert.ok(route.steps.length >= 3, district.id);
    assert.ok(route.steps.some(step => inventory.get(step.fileId).district === district.id), district.id);
  }
});

test('major-file behavior and all tour steps cite existing source lines', () => {
  for (const guide of index.files.values()) {
    for (const field of ['purpose', 'input', 'processing', 'output']) assert.ok(guide[field]?.trim(), `${guide.fileId}: ${field}`);
    validEvidence(guide.evidence, guide.fileId);
    for (const symbol of guide.symbols || []) {
      assert.ok(symbol.name && symbol.description && Number.isInteger(symbol.line), guide.fileId);
      assert.ok(symbol.line > 0 && symbol.line <= inventory.get(guide.fileId).lines, `${guide.fileId}: ${symbol.name}`);
    }
  }
  for (const route of index.tours.values()) {
    assert.ok(['reading', 'workflow'].includes(route.kind), route.id);
    for (const step of route.steps) {
      assert.ok(inventory.has(step.fileId) && step.title && step.description, route.id);
      assert.ok(step.line > 0 && step.line <= inventory.get(step.fileId).lines, route.id);
      validEvidence(step.evidence, route.id);
    }
  }
  for (const id of ['docs', 'showcase', 'verification']) {
    assert.equal(index.tours.get(index.districts.get(id).tourId).kind, 'reading');
  }
});

test('file routes include cross-district steps and retain the original chat walkthrough', () => {
  assert.equal(index.tours.get(original.id).steps.length, original.steps.length);
  const crossDistrict = [...index.tours.values()].find(route => new Set(route.steps.map(step => inventory.get(step.fileId).district)).size > 1);
  assert.ok(crossDistrict, 'at least one verified flow must bridge groups');
  const file = crossDistrict.steps.find(step => inventory.get(step.fileId).district !== crossDistrict.districtId);
  assert.ok(toursForFile(index, file.fileId).some(route => route.id === crossDistrict.id));
});

test('a new snapshot can drop files without leaving broken routes or key-file buttons', () => {
  const changed = { ...data, files: data.files.filter(file => file.id !== 'app.py') };
  const refreshed = createGuideIndex(changed, packs, original);
  assert.equal(refreshed.files.has('app.py'), false);
  assert.ok([...refreshed.districts.values()].every(guide => !guide.keyFiles.includes('app.py')));
  assert.ok([...refreshed.tours.values()].every(route => route.steps.every(step => step.fileId !== 'app.py')));
  assert.deepEqual(toursForFile(refreshed, 'app.py'), []);
});

import test from 'node:test';
import assert from 'node:assert/strict';
import { buildLayout, buildingHeight, createCollisionIndex, canStand } from '../src/layout.js';

const data = {
  districts: [{ id: 'app' }, { id: 'tests' }],
  files: Array.from({ length: 180 }, (_, i) => ({
    id: `file-${i}`, path: `src/file-${i}.py`, district: i < 100 ? 'app' : 'tests', lines: i * 20,
  })),
};

test('same metadata in different order keeps path-based layout deterministic', () => {
  const first = buildLayout(data), second = buildLayout({ ...data, files: data.files.slice().reverse() });
  assert.deepEqual(first.buildings.map(({ file, x, z }) => [file.id, x, z]),
    second.buildings.map(({ file, x, z }) => [file.id, x, z]));
});

test('districts and buildings never overlap and all files are represented', () => {
  const layout = buildLayout(data);
  assert.equal(layout.byId.size, data.files.length);
  for (const a of layout.districts) for (const b of layout.districts) {
    if (a === b) continue;
    assert.ok(a.x + a.width <= b.x || b.x + b.width <= a.x ||
      a.z + a.depth <= b.z || b.z + b.depth <= a.z);
  }
  for (const a of layout.buildings) for (const b of layout.buildings) {
    if (a === b) continue;
    assert.ok(Math.abs(a.x - b.x) >= (a.width + b.width) / 2 ||
      Math.abs(a.z - b.z) >= (a.depth + b.depth) / 2);
  }
});

test('walk collision blocks towers and map edges but allows a street beside a tower', () => {
  const layout = buildLayout(data), collision = createCollisionIndex(layout.buildings);
  const tower = layout.buildings[0];
  assert.equal(canStand(tower.x, tower.z, collision, layout.bounds), false);
  assert.equal(canStand(tower.x - 3.1, tower.z, collision, layout.bounds), true);
  assert.equal(canStand(layout.bounds.minX - 1, 0, collision, layout.bounds), false);
});

test('heights are monotonic, finite and capped even for enormous files', () => {
  const heights = [0, 10, 100, 1000, 10000000].map(buildingHeight);
  assert.ok(heights.every(Number.isFinite));
  assert.ok(heights.every((h, i) => i === 0 || h >= heights[i - 1]));
  assert.equal(heights.at(-1), 29);
});

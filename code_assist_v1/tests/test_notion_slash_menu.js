const assert = require('node:assert/strict');
const core = require('../notion_static/notion_editor_core.js');
const menu = require('../notion_static/notion_slash_menu.js');
const reference = require('../docs/NOTION_SLASH_REFERENCE_20260911.json');
const items = menu.definitions(core.filterBlockDefinitions(''));
const referenceItems = items.filter(item => item.type !== 'toggle_heading4' && item.group !== '추천'); // Keep the 2026-09-11 inventory historical.
assert.equal(reference.groups.reduce((sum, group) => sum + group.items.length, 0), 100);
assert.equal(reference.groups.length, 7);
assert.deepEqual(items.slice(0, 5).map(item => item.type), ['ai_notes', 'html_embed', 'table', 'heading1', 'paragraph']);
assert.equal(items.find(item => item.type === 'table' && item.group === '추천').detail, '데이터베이스');
assert.equal(items.filter(item => item.type === 'ai_notes').length, 2);
assert.equal(items.filter(item => item.type === 'html_embed').length, 2);
assert.notEqual(menu.icon('linked_database'), menu.icon('child_page'));
assert.equal(items.find(item => item.type === 'simple_table').label, '표');
assert.equal(items.find(item => item.type === 'list').label, '리스트 보기');
const originalDefinitions = core.filterBlockDefinitions('');
const originalDefinitionsBefore = structuredClone(originalDefinitions);
const aliasedItems = menu.definitions(originalDefinitions);
for (const item of aliasedItems) {
  const original = originalDefinitions.find(definition => definition.type === item.type);
  if (original) assert.ok(menu.matches(menu.searchText(item), original.label), original.label);
}
assert.deepEqual(originalDefinitions, originalDefinitionsBefore);
assert.ok(menu.matches(menu.searchText(items.find(item => item.type === 'list')), '/목록 보기'));
for (const item of referenceItems) {
  const observed = reference.groups.find(group => group.name === item.group).items;
  assert.ok(observed.includes(item.detail ? `${item.label} · ${item.detail}` : item.label), item.label);
  assert.ok(menu.icon(item.type).includes('<svg'));
  assert.ok(menu.matches(menu.searchText(item), item.label));
}
for (const group of [...new Set(referenceItems.map(item => item.group))]) {
  const observed = reference.groups.find(item => item.name === group).items;
  const positions = referenceItems.filter(item => item.group === group).map(item => observed.indexOf(item.detail ? `${item.label} · ${item.detail}` : item.label));
  assert.deepEqual(positions, [...positions].sort((a, b) => a - b));
}
assert.ok(items.some(item => item.type === 'heading4' && item.shortcut === '####'));
assert.ok(items.some(item => item.type === 'toggle_heading4' && item.label === '토글 제목4'));
assert.ok(menu.matches(menu.searchText(items.find(item => item.type === 'toggle_heading4')), '/제목4'));
assert.ok(menu.matches(menu.searchText(items.find(item => item.type === 'heading2')), '/제목 2'));
assert.ok(menu.matches(menu.searchText(items.find(item => item.type === 'heading2')), 'heading'));
assert.ok(!items.some(item => ['chart','dashboard','map','feed','form'].includes(item.type)));
console.log(`Notion slash reference: 100 observed entries; ${items.length} supported menu entries verified.`);
const values = new Map();
const storage = { getItem: key => values.get(key), setItem: (key, value) => values.set(key, value) };
let prefs = menu.preferences(storage, 'alice', 'workspace-a', 1000);
prefs.record('heading1'); prefs.record('heading1');
prefs = menu.preferences(storage, 'alice', 'workspace-a', 2000); prefs.record('equation');
let recommendations = menu.personalized(items, prefs.value).filter(item => item.group === '추천');
assert.equal(recommendations[0].type, 'heading1');
assert.equal(recommendations.find(item => item.type === 'html_embed').detail, '임베드');
assert.equal(recommendations.find(item => item.type === 'html_embed').badge, 'New');
prefs.value.mode = 'recent'; prefs.save();
assert.equal(menu.personalized(items, prefs.value)[0].type, 'equation');
assert.ok(prefs.toggle('heading2')); prefs.save();
assert.deepEqual(menu.personalized(items, prefs.value).slice(0, 2).map(item => item.type), ['heading2', 'equation']);
assert.ok(!menu.personalized(items, prefs.value).some(item => item.group === '고정'));
assert.deepEqual(menu.preferences(storage, 'alice', 'workspace-a', 2001).value.pins, ['heading2']);
assert.deepEqual(menu.preferences(storage, 'bob', 'workspace-a', 2001).value.pins, []);
assert.deepEqual(menu.preferences(storage, 'alice', 'workspace-b', 2001).value.events, []);
assert.equal(menu.preferences(storage, 'alice', 'workspace-a', 31 * 86400000).value.events.length, 0);
prefs.value.pins = ['table'];
assert.ok(!menu.personalized(items.filter(item => item.type !== 'table'), prefs.value).some(item => item.type === 'table'));
prefs.value.pins = [];
for (const type of ['heading1', 'heading2', 'heading3', 'todo', 'quote', 'equation', 'code', 'ai_notes']) assert.ok(prefs.toggle(type));
assert.equal(prefs.toggle('paragraph'), false);
assert.ok(prefs.toggle('heading1'));
assert.equal(menu.preferences({getItem() {throw Error();}, setItem() {throw Error();}}, 'alice', 'a').save(), false);
assert.deepEqual(menu.preferences({getItem() {return 'null';}}, 'alice', 'a').value.pins, []);
console.log('Slash preference isolation, ranking, pin limits and unavailable storage verified.');

"use strict";
const {test} = require("node:test");
const assert = require("node:assert/strict");
const icons = require("../notion_static/notion_icons.js");
const catalog = require("../notion_static/assets/icons/catalog.json");
icons.configure(catalog);

function memoryStorage() {
  const values = new Map();
  return {
    getItem: key => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
  };
}

test("recent icons follow the signed-in user across picker surfaces", () => {
  const storage = memoryStorage();
  const first = catalog.emojis[0].value;
  const second = catalog.emojis[1].value;
  const vector = icons.vectorValue(catalog.vectors[0].id, catalog.colors[1].id);
  icons.recordRecent("person-a", first, storage);
  icons.recordRecent("person-a", vector, storage);
  icons.recordRecent("person-a", second, storage);
  icons.recordRecent("person-a", first, storage);
  icons.recordRecent("person-b", second, storage);
  assert.deepEqual(icons.readRecent("person-a", storage), [first, second, vector]);
  assert.deepEqual(icons.readRecent("person-b", storage), [second]);
  assert.deepEqual(icons.readRecent("", storage), []);
});

test("recent icons exclude removed, unrecognized, and uploaded values", () => {
  const storage = memoryStorage();
  const valid = catalog.emojis[0].value;
  icons.recordRecent("person", valid, storage);
  icons.recordRecent("person", "", storage);
  icons.recordRecent("person", "nf:file_private", storage);
  icons.recordRecent("person", "<script>", storage);
  assert.deepEqual(icons.readRecent("person", storage), [valid]);
  icons.configure({...catalog, emojis: catalog.emojis.slice(1)});
  assert.deepEqual(icons.readRecent("person", storage), []);
  icons.configure(catalog);
});

test("storage failure cannot prevent selecting an icon", () => {
  const storage = {getItem() { throw Error("storage disabled"); }, setItem() { throw Error("storage disabled"); }};
  assert.doesNotThrow(() => icons.recordRecent("person", catalog.emojis[0].value, storage));
  assert.deepEqual(icons.readRecent("person", storage), []);
});

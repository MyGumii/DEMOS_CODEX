"use strict";
// UI-ICON-001: compact values, safe rendering, assets and upload validation.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const icons = require("../notion_static/notion_icons.js");
const catalog = require("../notion_static/assets/icons/catalog.json");
icons.configure(catalog);
for (const icon of catalog.vectors) for (const color of catalog.colors) {
  const value = icons.vectorValue(icon.id, color.id);
  assert(value.length <= 64);
  assert.deepEqual(icons.parse(value), {kind:"vector",id:icon.id,color:color.id});
  assert(icons.content(value).includes(`data-icon-color="${color.id}"`));
}
for (const emoji of catalog.emojis) {
  assert(fs.existsSync(path.join(__dirname, "../notion_static/assets/emoji", `${emoji.asset}.svg`)));
  assert(icons.content(emoji.value).includes(`${emoji.asset}.svg`));
}
const faces = catalog.emojis.filter(item => item.group === "사람" && !item.hidden);
assert.equal(faces.length, 85);
assert.deepEqual(faces.slice(0, 12).map(item => item.value),
  ["😀", "😃", "😄", "😁", "😆", "😅", "🤣", "😂", "🙂", "🙃", "🫠", "😉"]);
assert.equal(catalog.emojis.filter(item => item.hidden && item.toneBase).length, 25);
const houseSearch = ["🏠","🛖","🏘️","🪴","🪹","🏚️","🦞","😣","🐈","🫳","🙃","👌","🤌","🤏","🐚","🍵","🍺","🏡","🏮","🇪🇬"];
assert.deepEqual(catalog.observedSearch["집"], houseSearch);
assert.deepEqual(icons.searchItems(catalog.emojis.filter(item => !item.hidden), "집").map(item => item.value), houseSearch);
for (const base of ["✋", "👋", "💪", "👍", "🙏"]) {
  const item = catalog.emojis.find(entry => entry.value === base);
  assert(item.skinTone);
  for (let tone = 1; tone <= 5; tone++) {
    const variant = icons.tonedValue(item, tone);
    assert(catalog.emojis.some(entry => entry.value === variant && entry.hidden));
    assert(icons.content(variant).includes(".svg"));
  }
}
const storage = {data:new Map(),getItem(key){return this.data.get(key) ?? null;},setItem(key,value){this.data.set(key,value);}};
assert.equal(icons.readSkinTone("user-a", storage), 0);
icons.saveSkinTone("user-a", 3, storage);
assert.equal(icons.readSkinTone("user-a", storage), 3);
assert.equal(icons.readSkinTone("user-b", storage), 0);
assert(icons.content("<img src=x onerror=alert(1)>").includes("&lt;img"));
assert(!icons.content("ni:doc:red\" onload=evil").includes("onload"));
assert.equal(icons.parse("nf:../../secret").kind, "default");
assert.deepEqual(icons.parse(`ne:we_${"a".repeat(40)}`), {kind:"custom-emoji",id:`we_${"a".repeat(40)}`});
assert(icons.content(`ne:we_${"a".repeat(40)}`).includes(`/api/notion/custom-emoji/we_${"a".repeat(40)}`));
assert.equal(icons.parse("ne:we_../../secret").kind, "default");
assert.throws(() => icons.fileValue("https://external.test/image"));
assert.throws(() => icons.vectorValue("missing"));
assert(icons.content(icons.fileValue("f_abc123")).includes('/api/notion/files/f_abc123'));
assert(icons.content(icons.fileValue("file_abc123")).includes('/api/notion/collaboration/files/file_abc123'));
assert(icons.content("list").includes("<svg"));
assert.equal(icons.initial("👩‍💻 개발팀"), "👩‍💻");
assert(icons.workspace({name:"테스트",icon:"ni:building:blue"}).includes("<svg"));
(async () => {
  const image = (bytes, type) => new Blob([new Uint8Array(bytes)], {type});
  assert.equal(await icons.validateImage(image([137,80,78,71,13,10,26,10],"image/png")), "image/png");
  assert.equal(await icons.validateImage(image([255,216,255,224],"image/jpeg")), "image/jpeg");
  assert.equal(await icons.validateImage(new Blob(["RIFF0000WEBP"],{type:"image/webp"})), "image/webp");
  await assert.rejects(icons.validateImage(new Blob(["<svg/>"],{type:"image/png"})));
  await assert.rejects(icons.validateImage(image([255,216,255],"image/png")));
  await assert.rejects(icons.validateImage(new Blob([new Uint8Array(2*1024*1024+1)])));
  await assert.rejects(icons.validateImage(new Blob([])));
  console.log(`UI-ICON-001 passed: ${catalog.vectors.length * catalog.colors.length} vector/color combinations, ${catalog.emojis.length} assets, compatibility and file validation`);
})().catch(error => {console.error(error);process.exitCode=1;});

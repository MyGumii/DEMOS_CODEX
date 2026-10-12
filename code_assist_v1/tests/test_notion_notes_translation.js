const assert = require("node:assert/strict");
const translation = require("../notion_static/notion_notes_translation.js");
const notes = require("../notion_static/notion_ai_notes.js");
const modelList = [{id:"gemma4", provider:"hynix_api"}, {id:"qwen3.6", provider:"hynix_api"}, {id:"codex-local", provider:"codex"}];
assert.deepEqual(translation.allowedModels({models:modelList}).map(m => m.id), ["gemma4","qwen3.6"]);
assert.deepEqual(translation.allowedModels({models:modelList, codex_bridge:{enabled:false,connected:true}}).map(m => m.id), ["gemma4","qwen3.6"]);
assert.equal(translation.allowedModels({models:modelList, codex_bridge:{enabled:true,connected:true}}).length, 3);
assert.ok(!translation.autoLabel(modelList.slice(0,2)).includes("Codex"));
const policyController = new translation.Controller(); policyController.models = modelList;
assert.equal(policyController.activeModel({translation_model_id:"codex-local"}), "codex-local");
assert.equal(policyController.activeModel({translation_model_id:"gemma4"}), "gemma4");
policyController.models = translation.allowedModels({models:modelList, codex_bridge:{enabled:false,connected:true}});
assert.equal(policyController.activeModel({translation_model_id:"codex-local"}), "");
assert.equal(translation.allowedModels({models:modelList, codex_bridge:{enabled:true,connected:false}}).length, 2);
for (const source of ["x".repeat(9000), "😀".repeat(2400), "A sentence.\n".repeat(2000)]) {
  const parts = []; let remaining = source;
  while (remaining) { const part = translation.chunk(remaining); parts.push(part); remaining = remaining.slice(part.length); }
  assert.equal(parts.join(""), source);
  assert.ok(parts.every(part => part.length <= 2000 && !/[\uD800-\uDBFF]$/.test(part)));
}
const props = {transcript: "original", translation_source: "original", translation_text: "번역", translation_language: "한국어", translation_target: "한국어", translation_model_id: "codex-local"};
assert.equal(translation.stale(props), false);
assert.equal(translation.stale({...props, transcript: "original continued"}), false);
assert.equal(translation.stale({...props, transcript: "changed"}), true);
assert.equal(translation.stale({...props, translation_target: "English"}), true);
assert.equal(notes.exportFile(props, "translation", "txt").text, "번역");
assert.ok(notes.exportFile(props, "all", "md").text.includes("번역"));
assert.ok(notes.toMarkdown(props).includes("번역"));
assert.equal(notes.clearedProps(props, "all").translation_text, undefined);
assert.equal(notes.clearedProps(props, "all").translation_model_id, "codex-local");
assert.equal(notes.clearedProps(props, "transcript").translation_text, "번역");
assert.ok(!translation.html({...props, translation_text: "<script>bad</script>"}, false).includes("<script>"));
const pairProps = {translation_mode: "pair", translation_pair: ["ko", "ja"], transcript: "こんにちは。"};
assert.deepEqual(translation.pair(pairProps), ["ko", "ja"]);
assert.deepEqual(translation.pair({translation_pair: ["ko", "ko"]}), []);
assert.deepEqual(translation.pair({translation_pair: ["ko", "zh"]}), []);
assert.equal(translation.configKey(pairProps), translation.configKey({...pairProps, translation_pair:["ja", "ko"]}));
assert.equal(translation.audioLanguage(pairProps, "ko"), "");
assert.equal(translation.audioLanguage({}, "ko"), "ko");
const completedPair = {...pairProps, translation_source: pairProps.transcript, translation_text: "[일본어 → 한국어]\n안녕하세요.",
  translation_language: "한국어 ↔ 일본어", translation_config: translation.configKey(pairProps)};
assert.equal(translation.stale(completedPair), false);
assert.equal(translation.stale({...completedPair, translation_pair:["ko", "en"]}), true);
assert.equal(translation.stale({...completedPair, translation_mode:"single", translation_target:"한국어 ↔ 일본어"}), true);
assert.equal(notes.clearedProps(completedPair, "all").translation_config, undefined);
assert.deepEqual(notes.clearedProps(completedPair, "all").translation_pair, ["ko", "ja"]);
assert.ok(notes.exportFile(completedPair, "translation", "txt").text.includes("일본어 → 한국어"));
console.log("Translation chunk/export/clear tests passed");
async function controllerTests() {
  const draft = {transcript: "First sentence.", translation_target: "한국어"};
  const calls = []; let response, saves = 0, failSave = false;
  const controller = new translation.Controller();
  const node = {isConnected: true};
  const adapter = {scope: {page_id: "synthetic"}, getBlock: () => ({type: "ai_notes", props: draft}), canEdit: () => true,
    request: async (_path, options) => { calls.push(options.body); return new Promise(resolve => { response = resolve; }); }};
  controller.views.set(node, {adapter, blocked: () => false, mutate: fn => fn(draft), persist: async () => { saves++; return !failSave; }, sync: () => {}});
  const tick = () => new Promise(resolve => setImmediate(resolve));
  const run = controller.run();
  while (!response) await tick();
  draft.transcript += " Appended sentence.";
  response({text: calls[0].text, translation: "첫 문장.", target_language: "한국어"});
  await run;
  assert.equal(calls.length, 1);
  assert.equal(draft.translation_source, "First sentence.");
  assert.ok(controller.message.includes("추가 원문"));
  response = null; const second = controller.run();
  while (!response) await tick();
  assert.equal(calls[1].text, " Appended sentence.");
  failSave = true;
  response({text: calls[1].text, translation: "추가 문장.", target_language: "한국어"});
  await second;
  assert.ok(controller.message.includes("저장하지 못했습니다"));
  failSave = false;
  const before = saves;
  await controller.run();
  assert.ok(saves > before && calls.length === 2);
  assert.equal(controller.message, "현재 원문 번역 완료 · 저장됨");
  draft.transcript += " Canceled sentence.";
  response = null; const third = controller.run();
  while (!response) await tick();
  controller.stop();
  response({text: calls[2].text, translation: "반영하면 안 됨", target_language: "한국어"});
  await third;
  assert.ok(!draft.translation_text.includes("반영하면 안 됨"));
  assert.ok(controller.message.includes("중지했습니다"));
  Object.assign(draft, pairProps);
  for (const field of translation.fields) delete draft[field];
  response = null; const paired = controller.run();
  while (!response) await tick();
  assert.deepEqual(calls[3].language_pair, ["ko", "ja"]);
  assert.equal(calls[3].translation_mode, "pair");
  assert.equal(calls[3].target_language, "한국어 ↔ 일본어");
  response({text: calls[3].text, translation: "[일본어 → 한국어]\n안녕", target_language:"한국어 ↔ 일본어", translation_mode:"pair", language_pair:["ko","ja"]});
  await paired;
  assert.equal(draft.translation_config, translation.configKey(pairProps));
  assert.equal(translation.stale(draft), false);
  draft.transcript += "안녕하세요.";
  response = null; const wrongPair = controller.run();
  while (!response) await tick();
  response({text: calls[4].text, translation: "bad pair", target_language:"한국어 ↔ 일본어", translation_mode:"pair", language_pair:["ko","en"]});
  await wrongPair;
  assert.ok(controller.message.includes("언어 쌍이 일치하지"));
  assert.ok(!draft.translation_text.includes("bad pair"));
  response = null; const changedPair = controller.run();
  while (!response) await tick();
  draft.translation_pair = ["ko", "en"];
  response({text: calls[5].text, translation: "late old pair", target_language:"한국어 ↔ 일본어", translation_mode:"pair", language_pair:["ko","ja"]});
  await changedPair;
  assert.ok(controller.message.includes("결과를 반영하지"));
  assert.ok(!draft.translation_text.includes("late old pair"));
  console.log("Translation incremental/save-failure/cancel controller tests passed");
}
async function delayedSaveTests() {
  for (const outcome of ["saved", "failed", "stopped"]) {
    const draft = {transcript: "Synthetic delayed save.", translation_target: "한국어"};
    const controller = new translation.Controller();
    controller.auto = true;
    let release, calls = 0, painted = "";
    const adapter = {scope: {page_id: "isolated"}, canEdit: () => true,
      getBlock: () => ({type: "ai_notes", props: draft}),
      request: async (_path, options) => { calls++; return {text: options.body.text, target_language: "한국어", translation: "지연 저장 테스트"}; }};
    const view = {adapter, blocked: () => false, mutate: fn => fn(draft),
      persist: () => draft.translation_text ? new Promise(resolve => { release = resolve; }) : Promise.resolve(true),
      sync: () => { painted = draft.translation_text || ""; }};
    controller.views.set({isConnected: true}, view);
    const run = controller.run();
    for (let i = 0; !release && i < 50; i++) await new Promise(resolve => setImmediate(resolve));
    assert.ok(release, "result persistence should be pending");
    assert.equal(painted, "지연 저장 테스트", "received text must paint without toggling auto off");
    assert.equal(controller.auto, true);
    assert.ok(controller.message.includes("저장 중"));
    if (outcome === "stopped") controller.stop();
    release(outcome !== "failed");
    await run;
    assert.equal(painted, "지연 저장 테스트");
    assert.ok(controller.message.includes(outcome === "saved" ? "새 전사를 기다리는 중" : outcome === "failed" ? "저장하지 못했습니다" : "중지했습니다"));
    if (outcome === "failed") {
      assert.equal(controller.auto, false);
      view.persist = async () => true;
      await controller.run();
      assert.equal(calls, 1, "save retry must not retranslate the retained result");
    }
  }
  console.log("Translation paint-before-save/saved/failed/stopped tests passed");
}
async function batchedAutoChunks() {
  const draft = {transcript: "한글 일본어 😀.\n".repeat(400), translation_target: "日本語"};
  const controller = new translation.Controller(); controller.auto = true;
  const calls = [];
  const adapter = {scope: {page_id: "isolated"}, canEdit: () => true, getBlock: () => ({type: "ai_notes", props: draft}),
    request: async (_path, options) => { calls.push(options.body); return {text: options.body.text, target_language: "日本語", translation: "synthetic"}; }};
  controller.views.set({isConnected: true}, {adapter, blocked: () => false, mutate: fn => fn(draft), persist: async () => true, sync() {}});
  await controller.run();
  assert.equal(calls.map(call => call.text).join(""), draft.transcript);
  assert.ok(calls.length > 1 && calls.every(call => call.text.length <= 2000 && call.context.length <= 1200));
  assert.ok(calls.every(call => !/[\uD800-\uDBFF]$/.test(call.text)));
  assert.equal(draft.translation_source, draft.transcript);
  assert.ok(controller.lastRequestMs >= 0);
  assert.equal(controller.requestStartedAt, null);
  controller.stop();
  console.log("Batched automatic chunks/exact coverage/latency cleanup tests passed");
}
async function recordingScopeTests() {
  const old = "[00:00] OLD TWO SECOND\n[00:02] OLD FRAGMENT\n[00:04] OLD LAST";
  const fresh = "\n[00:00] New complete twelve second sentence.\n[00:12] Next twelve second sentence.";
  const draft = {transcript: old + fresh, audio_started_at: "new-meeting", translation_target: "日本語",
    translation_source: "[00:00] OLD TWO SECOND", translation_text: "OLD TRANSLATION", translation_language: "日本語"};
  // Detect the boundary in existing saved notes created before scope metadata.
  assert.equal(translation.recordingStart(draft), old.length + 1);
  assert.deepEqual(translation.displayedTranslation(draft), {current: "", previous: "OLD TRANSLATION"});
  const controller = new translation.Controller(); controller.auto = true;
  const calls = [];
  const adapter = {scope: {page_id: "isolated"}, canEdit: () => true, getBlock: () => ({type: "ai_notes", props: draft}),
    request: async (_path, options) => { calls.push(options.body); return {text: options.body.text, target_language: "日本語", translation: "NEW TRANSLATION"}; }};
  const view = {adapter, blocked: () => false, mutate: fn => fn(draft), persist: async () => true, sync() {}};
  controller.views.set({isConnected: true}, view);
  await controller.run();
  assert.equal(calls.length, 1);
  assert.equal(calls[0].text, fresh.slice(1));
  assert.equal(calls[0].context, "");
  assert.equal(draft.transcript, old + fresh);
  assert.deepEqual(translation.displayedTranslation(draft), {current:"NEW TRANSLATION", previous:"OLD TRANSLATION"});
  assert.ok(notes.exportFile(draft, "translation", "txt").text.includes("OLD TRANSLATION"));
  assert.equal(draft.translation_source, draft.transcript);
  // Persisted/reloaded scope does not retransmit either old or current text.
  await controller.run(); assert.equal(calls.length, 1);
  controller.stop(); draft.translation_include_history = true;
  await controller.run();
  assert.equal(calls[1].text, old + fresh);
  assert.equal(translation.sourceScope(draft).key, "all");
  // New meeting fence rejects a late model answer even if it ignores AbortSignal.
  let release;
  draft.transcript += " unfinished";
  adapter.request = async (_path, options) => new Promise(resolve => { release = () => resolve({text: options.body.text, target_language: "日本語", translation: "LATE OLD RESULT"}); });
  const run = controller.run();
  for (let n = 0; !release && n < 50; n++) await new Promise(resolve => setImmediate(resolve));
  assert.ok(release);
  draft.audio_transcript_start = draft.transcript.length;
  draft.audio_started_at = "newer-meeting";
  draft.translation_include_history = false;
  controller.sourceChanged();
  release(); await run;
  assert.ok(!draft.translation_text.includes("LATE OLD RESULT"));
  const cleared = notes.clearedProps(draft, "all");
  assert.equal(cleared.translation_scope_key, undefined);
  assert.equal(cleared.audio_transcript_start, undefined);
  assert.equal(translation.recordingStart({...draft, audio_scope_invalid:true}), 0);
  controller.stop();
  console.log("Old 2-second backlog isolation/current recording/reload/history/late response tests passed");
}
controllerTests().then(delayedSaveTests).then(batchedAutoChunks).then(recordingScopeTests).catch(error => { console.error(error); process.exitCode = 1; });

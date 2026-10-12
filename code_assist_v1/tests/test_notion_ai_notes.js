const assert = require("node:assert/strict");
const notes = require("../notion_static/notion_ai_notes.js");
const core = require("../notion_static/notion_editor_core.js");
const block = core.createBlock("ai_notes");
assert.equal(block.type, "ai_notes");
assert.equal(block.props.transcript, "");
assert.ok(core.filterBlockDefinitions("회의").some(item => item.type === "ai_notes"));
block.props = { transcript: "<script>alert(1)</script>", notes: "memo", result: {
  summary: '<img src=x onerror="alert(1)">', decisions: [],
  action_items: [{task: "review", owner: "tester", due: "Friday", source_quote: 'quote\" onclick="alert(1)', done: true}],
}};
const html = notes.html(block, true);
assert.ok(html.includes("&lt;script&gt;"));
assert.ok(!html.includes("<script>"));
assert.ok(!html.includes("<img"));
assert.ok(html.includes("&quot; onclick=&quot;"));
const readonly = notes.html(block, false);
assert.ok(!readonly.includes("data-ai-generate"));
assert.ok(!readonly.includes("data-ai-import"));
assert.ok(readonly.includes("readonly"));
assert.ok(readonly.includes("disabled"));
assert.ok(notes.toMarkdown(block.props).includes("- [x] review"));
assert.ok(notes.toMarkdown(block.props).includes("<script>alert(1)</script>"));
assert.notEqual(notes.sourceKey({transcript: "before"}), notes.sourceKey({transcript: "after"}));
const exportProps = { transcript: "  [00:12] 한국어 원문 😀\r\n두 번째 줄\n", notes: "메모만\n", instructions: "DO NOT EXPORT INSTRUCTIONS",
  model_id: "PRIVATE_MODEL_METADATA", result: { summary: "요약 본문 <script>not code</script>",
    decisions: [{text: "출시 결정", source_quote: "두 번째 줄"}],
    action_items: [{task: "보고서 작성", owner: "민수", due: "금요일", done: true, source_quote: "한국어 원문"}] },
  summary_phase: "provisional", generated_source: notes.sourceKey({transcript: "older source"}) };
const originalProps = JSON.stringify(exportProps);
const when = new Date("2026-09-11T10:20:30.000Z");
assert.equal(notes.exportFile(exportProps, "transcript", "txt", when).text, exportProps.transcript);
assert.equal(notes.exportFile(exportProps, "notes", "txt", when).text, exportProps.notes);
const summaryFile = notes.exportFile(exportProps, "summary", "txt", when);
assert.ok(summaryFile.text.includes("출시 결정") && summaryFile.text.includes("[x] 보고서 작성 · 담당: 민수 · 기한: 금요일"));
assert.ok(summaryFile.text.includes("근거: 한국어 원문"));
assert.ok(summaryFile.text.includes("[잠정 요약]") && summaryFile.text.includes("[이전 요약]"));
assert.ok(!summaryFile.text.includes("메모만") && !summaryFile.text.includes("[00:12]"));
const allFile = notes.exportFile(exportProps, "all", "md", when);
assert.ok(allFile.text.includes("# AI 노트") && allFile.text.includes("## 회의록 원문"));
assert.ok(allFile.text.includes("메모만") && allFile.text.includes("한국어 원문 😀"));
assert.ok(!allFile.text.includes("<script>") && allFile.text.includes("&lt;script&gt;"));
assert.ok(!allFile.text.includes("DO NOT EXPORT") && !allFile.text.includes("PRIVATE_MODEL"));
assert.equal(allFile.filename, "AI노트_전체노트_2026-09-11T10-20-30-000Z.md");
assert.equal(notes.exportFile({}, "all"), null);
assert.equal(notes.exportFile({transcript: " \n"}, "transcript"), null);
assert.equal(notes.exportFile(exportProps, "unknown"), null);
assert.equal(notes.exportFile(exportProps, "all", "html"), null);
assert.equal(notes.exportFile(exportProps, "__proto__"), null);
assert.equal(JSON.stringify(exportProps), originalProps);
assert.ok(notes.hasExportContent({result: {decisions: [{text: "decision without summary"}]}}, "summary"));
assert.ok(readonly.includes("data-ai-export"));
assert.ok(!readonly.includes("data-ai-clear="));
assert.ok(html.includes('data-ai-clear="all"') && html.includes('data-ai-clear="summary"'));
const clearProps = { ...exportProps, audio_started_at: "meeting start", source_sha256: "digest", model_name: "Gemma4", provider: "hynix_api",
  generated_at: "generated time", output_language: "ko", ai_notes_version: 1, extra_future_setting: { keep: true } };
const clearBefore = JSON.stringify(clearProps);
const clearedTranscript = notes.clearedProps(clearProps, "transcript");
assert.equal(clearedTranscript.transcript, "");
assert.ok(!Object.hasOwn(clearedTranscript, "audio_started_at"));
assert.deepEqual(clearedTranscript.result, clearProps.result);
assert.equal(clearedTranscript.notes, clearProps.notes);
assert.notEqual(notes.sourceKey(clearedTranscript), notes.sourceKey(clearProps));
const clearedNotes = notes.clearedProps(clearProps, "notes");
assert.equal(clearedNotes.notes, "");
assert.equal(clearedNotes.transcript, clearProps.transcript);
const clearedSummary = notes.clearedProps(clearProps, "summary");
for (const field of ["result", "summary_phase", "generated_source", "source_sha256", "model_name", "provider", "generated_at"]) {
  assert.ok(!Object.hasOwn(clearedSummary, field));
}
assert.equal(clearedSummary.transcript, clearProps.transcript);
assert.equal(clearedSummary.notes, clearProps.notes);
const clearedAll = notes.clearedProps(clearProps, "all");
assert.equal(clearedAll.transcript, "");
assert.equal(clearedAll.notes, "");
assert.equal(notes.hasClearContent(clearedAll, "all"), false);
for (const field of ["instructions", "model_id", "output_language", "ai_notes_version", "extra_future_setting"]) assert.deepEqual(clearedAll[field], clearProps[field]);
assert.equal(notes.toMarkdown(clearedAll), "## AI 노트");
assert.equal(notes.exportFile(clearedAll, "all"), null);
assert.equal(JSON.stringify(clearProps), clearBefore);
assert.ok(notes.hasClearContent({transcript: " \n"}, "transcript"));
assert.equal(notes.clearedProps({}, "all"), null);
assert.equal(notes.clearedProps(clearProps, "__proto__"), null);
assert.equal(notes.clearedProps(clearProps, "unknown"), null);
console.log("AI notes core tests passed");
const edited = notes.editedSummary(exportProps, "직접 수정한 요약\n두 번째 줄", when.toISOString());
assert.equal(edited.result.summary, "직접 수정한 요약\n두 번째 줄");
assert.deepEqual(edited.result.decisions, exportProps.result.decisions);
assert.deepEqual(edited.result.action_items, exportProps.result.action_items);
assert.equal(edited.transcript, exportProps.transcript);
assert.equal(edited.notes, exportProps.notes);
assert.equal(edited.generated_source, exportProps.generated_source);
assert.equal(edited.summary_manually_edited, true);
assert.equal(edited.summary_edited_at, when.toISOString());
assert.equal(JSON.stringify(exportProps), originalProps);
assert.notEqual(notes.summaryEditKey(exportProps), notes.summaryEditKey(edited));
assert.ok(notes.exportFile(edited, "summary", "txt").text.includes("[직접 수정한 요약]"));
assert.ok(notes.html({props: edited}, true).includes('data-ai-field="summary"'));
assert.ok(!notes.html({props: edited}, false).includes('data-ai-field="summary"'));
assert.ok(!notes.html({props: {...edited, comparison_enabled: true}}, true).includes('data-ai-field="summary"'));
assert.ok(notes.html({props: notes.editedSummary(edited, "")}, true).includes('data-ai-field="summary"'));
for (const props of [{}, {...exportProps, comparison_enabled: true}]) assert.equal(notes.editedSummary(props, "new"), null);
for (const value of [null, 1, "bad\0text", "x".repeat(12001)]) assert.equal(notes.editedSummary(exportProps, value), null);
for (const kind of ["summary", "all"]) {
  const cleared = notes.clearedProps(edited, kind);
  assert.ok(!Object.hasOwn(cleared, "summary_manually_edited"));
  assert.ok(!Object.hasOwn(cleared, "summary_edited_at"));
}
console.log("AI notes direct summary edit/preservation/visibility tests passed");
assert.equal(notes.captureSeconds({local_only: true, chunk_seconds: 12}), 12);
assert.equal(notes.captureSeconds({local_only: true}, {audio_chunk_seconds: 10}), 12);
assert.equal(notes.captureSeconds({local_only: true}, {audio_chunk_seconds: 12}), 12);
for (const value of [0, 1, 2, 3, 4, 8, "2", "10", -1]) assert.equal(notes.captureSeconds({local_only: true}, {audio_chunk_seconds: value}), 12);
assert.equal(notes.captureSeconds({local_only: false, chunk_seconds: 12}, {audio_chunk_seconds: 2}), 12);
assert.equal(notes.captureSeconds({chunk_seconds: 1}), 1);
assert.equal(notes.captureMode({local_only: true}), "speech");
assert.equal(notes.captureMode({local_only: true}, {audio_chunk_seconds: 12}), "speech");
assert.equal(notes.captureMode({local_only: true}, {audio_chunk_mode: "speech"}), "speech");
assert.equal(notes.captureMode({local_only: true}, {audio_chunk_mode: "fixed"}), "fixed");
assert.equal(notes.captureMode({local_only: false}, {audio_chunk_mode: "speech"}), "fixed");
assert.equal(notes.clearedProps({...exportProps, audio_chunk_seconds: 4}, "all").audio_chunk_seconds, 4);
console.log("Local speech/fixed 12-second modes/legacy fixed setting normalization tests passed");

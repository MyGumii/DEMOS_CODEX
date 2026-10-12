const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const { wav, Meeting } = require("../notion_static/notion_meeting_audio.js");
const { sourceKey, canApplySummary } = require("../notion_static/notion_ai_notes.js");
const tick = () => new Promise(resolve => setImmediate(resolve));

function fixture(overrides = {}) {
  const state = { texts: [], calls: [], stops: 0, finals: 0 };
  const meeting = new Meeting({
    valid: () => true,
    transcribe: async item => { state.calls.push(item.id); return { text: `text-${item.id}` }; },
    onText: text => state.texts.push(text), persist: async () => true,
    onStopped: () => { state.finals++; },
    captureFactory: (chunk, error) => {
      state.chunk = chunk; state.error = error;
      return { start: async () => {}, stop: async () => { state.stops++; }, pause() {}, resume() {} };
    }, ...overrides,
  });
  return { meeting, state };
}

(async () => {
  const view = new DataView(await wav(new Float32Array([-2, -1, 0, 1, 2, NaN]), 16000).arrayBuffer());
  assert.equal(view.byteLength, 56);
  assert.equal(view.getUint32(24, true), 16000);
  assert.equal(view.getUint32(40, true), 12);
  assert.deepEqual(Array.from({ length: 6 }, (_, i) => view.getInt16(44 + i * 2, true)), [-32768, -32768, 0, 32767, 32767, 0]);
  const snapshot = sourceKey({ transcript: "before", notes: "memo" });
  assert.ok(canApplySummary(snapshot, { transcript: "before appended", notes: "memo" }, true));
  assert.ok(!canApplySummary(snapshot, { transcript: "before appended", notes: "memo" }, false));
  assert.ok(!canApplySummary(snapshot, { transcript: "rewritten", notes: "memo" }, true));
  assert.ok(!canApplySummary(snapshot, { transcript: "before", notes: "changed" }, true));

  // Actual worklet processes variable-size blocks, mixes channels and flushes tail on pause/stop.
  const output = [];
  let Processor;
  vm.runInNewContext(fs.readFileSync(require.resolve("../notion_static/notion_audio_worklet.js"), "utf8"), {
    sampleRate: 4, Float32Array,
    AudioWorkletProcessor: class { constructor() { this.port = { postMessage: value => output.push(value) }; } },
    registerProcessor: (name, ctor) => { Processor = ctor; },
  });
  const processor = new Processor({ processorOptions: { seconds: 1 } });
  processor.process([[new Float32Array([1, 1, 1]), new Float32Array([-1, -1, -1])]]);
  processor.port.onmessage({ data: "pause" });
  assert.deepEqual([...output[0].samples], [0, 0, 0]);
  processor.process([[new Float32Array([1, 1])]]);
  processor.port.onmessage({ data: "resume" });
  processor.process([[new Float32Array([1, 2, 3, 4, 5])]]);
  processor.port.onmessage({ data: "stop" });
  assert.deepEqual([...output[1].samples], [1, 2, 3, 4]);
  assert.deepEqual([...output[2].samples], [5]);
  assert.ok(output[3].stopped);
  assert.equal(processor.process([]), false);

  const ordered = fixture();
  let starts = 0;
  ordered.meeting.options.onStarted = () => { starts++; assert.ok(ordered.meeting.startedAt); assert.equal(ordered.state.calls.length, 0); };
  await ordered.meeting.start();
  assert.equal(starts, 1);
  ordered.state.chunk(new Blob(["a"]), 12);
  ordered.state.chunk(new Blob(["b"]), 4);
  ordered.meeting.pause(); assert.equal(ordered.meeting.phase, "paused");
  ordered.meeting.resume(); assert.equal(ordered.meeting.phase, "recording");
  await Promise.all([ordered.meeting.stop(), ordered.meeting.stop()]);
  assert.equal(ordered.state.calls.length, 2);
  assert.equal(ordered.state.texts.length, 2);
  assert.equal(ordered.state.finals, 1);
  assert.equal(ordered.state.stops, 1);
  assert.equal(ordered.meeting.duration, 16);
  assert.ok(ordered.meeting.lastTranscribeMs >= 0 && ordered.meeting.lastSaveMs >= 0);

  let saves = 0;
  const retry = fixture({ persist: async () => ++saves > 1 });
  await retry.meeting.start(); retry.state.chunk(new Blob(["test"]), 1);
  await retry.meeting.drain();
  assert.equal(retry.meeting.phase, "error"); assert.equal(retry.meeting.queue.length, 1);
  assert.equal(retry.state.texts.length, 1);
  await retry.meeting.retry();
  assert.equal(retry.state.texts.length, 1); assert.equal(retry.state.calls.length, 1);
  assert.equal(retry.meeting.queue.length, 0); assert.equal(retry.state.finals, 1);

  const tail = fixture({ captureFactory: chunk => ({ start: async () => {}, stop: async () => { chunk(new Blob(["tail"]), 0.5); } }) });
  await tail.meeting.start(); await tail.meeting.stop();
  assert.equal(tail.state.texts.length, 1); assert.equal(tail.state.finals, 1);

  let resolveRequest, signal;
  const stale = fixture({ transcribe: (item, givenSignal) => { signal = givenSignal; return new Promise(resolve => { resolveRequest = resolve; }); } });
  await stale.meeting.start(); stale.state.chunk(new Blob(["a"]), 1);
  await stale.meeting.dispose(); resolveRequest({ text: "must not append" }); await tick();
  assert.ok(signal.aborted); assert.equal(stale.state.texts.length, 0); assert.equal(stale.meeting.queue.length, 0);

  const denied = fixture({ captureFactory: () => ({ start: async () => { throw new DOMException("denied", "NotAllowedError"); }, stop: async () => {} }) });
  await denied.meeting.start(); assert.equal(denied.meeting.phase, "error");
  assert.match(denied.meeting.message, /권한이 거부/); assert.equal(denied.state.calls.length, 0);

  const pressure = fixture({ transcribe: () => new Promise(resolve => { resolveRequest = resolve; }) });
  await pressure.meeting.start();
  for (let i = 0; i < 10; i++) pressure.state.chunk(new Blob(["a"]), 12);
  assert.equal(pressure.meeting.phase, "error"); assert.equal(pressure.meeting.queue.length, 10);
  assert.equal(pressure.state.stops, 1);
  await pressure.meeting.dispose(); resolveRequest({ text: "ignored" }); await tick();
  console.log("Meeting audio WAV/worklet/lifecycle/retry/backpressure tests passed");
})().catch(error => { console.error(error); process.exitCode = 1; });

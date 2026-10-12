const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function capture(mode = 'speech', seconds = 12) {
  const emitted = [], input = [];
  let Processor;
  vm.runInNewContext(fs.readFileSync(require.resolve('../notion_static/notion_audio_worklet.js'), 'utf8'), {
    sampleRate: 16000, Float32Array,
    AudioWorkletProcessor: class { constructor() { this.port = { postMessage: v => emitted.push(v) }; } },
    registerProcessor: (_name, ctor) => { Processor = ctor; },
  });
  const processor = new Processor({processorOptions: {seconds, boundaryMode: mode}});
  function feed(seconds, amplitude = .2) {
    const samples = Float32Array.from({length: Math.round(16000 * seconds)}, (_, i) => amplitude ? amplitude * Math.sin(i * .2) : 0);
    input.push(samples);
    for (let at = 0; at < samples.length; at += 128) processor.process([[samples.subarray(at, at + 128)]]);
  }
  function stop() { processor.port.onmessage({data: 'stop'}); }
  function lossless() {
    const expected = Buffer.concat(input.map(v => Buffer.from(v.buffer)));
    const actual = Buffer.concat(emitted.filter(v => v.samples).map(v => Buffer.from(v.samples.buffer)));
    assert.ok(actual.equals(expected), 'every input PCM sample emitted once, in order');
  }
  return {emitted, feed, stop, lossless, processor};
}

const natural = capture();
natural.feed(2); natural.feed(.8, 0);
assert.equal(natural.emitted.length, 0, 'short pauses do not restore 2-second fragments');
natural.feed(9.2); // 12 seconds of accumulated context; still talking.
assert.equal(natural.emitted.length, 0, 'do not cut speech at the old fixed 12s boundary');
natural.feed(.8, 0);
assert.equal(natural.emitted[0].samples.length, 12.8 * 16000);
assert.equal(natural.emitted[0].reason, 'pause');
natural.feed(1.3); natural.stop(); natural.lossless();
assert.equal(natural.emitted[1].reason, 'tail');

const nonstop = capture();
nonstop.feed(19.8); nonstop.feed(.2, 0);
assert.equal(nonstop.emitted[0].samples.length, 20 * 16000);
assert.equal(nonstop.emitted[0].reason, 'limit');
nonstop.feed(.6, 0); nonstop.feed(1); nonstop.stop(); nonstop.lossless();

const briefPause = capture();
briefPause.feed(12); briefPause.feed(.3, 0); briefPause.feed(1);
assert.equal(briefPause.emitted.length, 0, 'a short breath does not finalize the utterance');
briefPause.feed(.8, 0); briefPause.stop(); briefPause.lossless();

for (const interval of [10, 12]) {
  const fixed = capture('fixed', interval);
  fixed.feed(interval); assert.equal(fixed.emitted[0].samples.length, interval * 16000);
  assert.equal(fixed.emitted[0].reason, 'fixed');
  fixed.feed(.3); fixed.stop(); fixed.lossless();
}
const pauseResume = capture();
pauseResume.feed(4.4); pauseResume.processor.port.onmessage({data:'pause'});
pauseResume.processor.port.onmessage({data:'resume'}); pauseResume.feed(10); pauseResume.feed(.8, 0);
pauseResume.stop(); pauseResume.lossless();
assert.equal(pauseResume.emitted[1].samples.length, 10.8 * 16000);
console.log('Acoustic endpoint/minimum batch/20s cap/fixed rollback/lossless tail tests passed');

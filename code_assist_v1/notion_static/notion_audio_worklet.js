/* Continuous mono PCM capture. Each emitted buffer is an independent segment. */
class NotionPcmCapture extends AudioWorkletProcessor {
  constructor(options) {
    super();
    this.speechBoundary = options.processorOptions?.boundaryMode === "speech";
    this.samples = new Float32Array(Math.round(sampleRate * (this.speechBoundary ? 20 : (options.processorOptions?.seconds || 12))));
    // Acoustic endpointing, not semantic sentence classification. Keep every
    // sample, including quiet speech and music; no additional network/LLM call.
    this.frameSize = Math.max(1, Math.round(sampleRate * .02));
    this.frameUsed = 0;
    this.frameEnergy = 0;
    this.quietSamples = 0;
    this.noiseFloor = .001;
    this.used = 0;
    this.paused = false;
    this.stopped = false;
    this.port.onmessage = ({ data }) => {
      if (data === "pause" || data === "stop") this.flush();
      if (data === "pause") this.paused = true;
      if (data === "resume") this.paused = false;
      if (data === "stop") { this.stopped = true; this.port.postMessage({ stopped: true }); }
    };
  }
  flush(reason = "tail") {
    if (!this.used) return;
    const samples = this.samples.slice(0, this.used);
    this.port.postMessage({ samples, sampleRate, reason }, [samples.buffer]);
    this.used = 0;
    this.quietSamples = 0;
    this.frameUsed = 0;
    this.frameEnergy = 0;
  }
  process(inputs) {
    if (this.stopped) return false;
    const channels = inputs[0];
    if (this.paused || !channels?.length) return true;
    for (let i = 0; i < channels[0].length; i++) {
      let value = 0;
      for (const channel of channels) value += channel[i] || 0;
      value /= channels.length;
      this.samples[this.used++] = value;
      if (this.speechBoundary) {
        this.frameEnergy += value * value;
        this.frameUsed++;
        if (this.frameUsed >= this.frameSize) {
          const rms = Math.sqrt(this.frameEnergy / this.frameUsed);
          const quiet = rms < Math.max(.006, Math.min(.025, this.noiseFloor * 3));
          this.quietSamples = quiet ? this.quietSamples + this.frameUsed : 0;
          if (quiet) this.noiseFloor = .98 * this.noiseFloor + .02 * rms;
          this.frameUsed = 0; this.frameEnergy = 0;
          // Do not return to tiny chunks: accumulate >=10s, then wait for a
          // continuous 800ms pause. Continuous sound is bounded at 20s.
          if (this.used >= sampleRate * 10 && this.quietSamples >= sampleRate * .8) this.flush("pause");
        }
      }
      if (this.used === this.samples.length) this.flush(this.speechBoundary ? "limit" : "fixed");
    }
    // Outputs remain silent: never play the user's microphone back.
    return true;
  }
}
registerProcessor("notion-pcm-capture", NotionPcmCapture);

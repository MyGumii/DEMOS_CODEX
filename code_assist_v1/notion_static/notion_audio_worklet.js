/* Continuous mono PCM capture. Each emitted buffer is an independent segment. */
class NotionPcmCapture extends AudioWorkletProcessor {
  constructor(options) {
    super();
    this.samples = new Float32Array(Math.round(sampleRate * (options.processorOptions?.seconds || 12)));
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
  flush() {
    if (!this.used) return;
    const samples = this.samples.slice(0, this.used);
    this.port.postMessage({ samples, sampleRate }, [samples.buffer]);
    this.used = 0;
  }
  process(inputs) {
    if (this.stopped) return false;
    const channels = inputs[0];
    if (this.paused || !channels?.length) return true;
    for (let i = 0; i < channels[0].length; i++) {
      let value = 0;
      for (const channel of channels) value += channel[i] || 0;
      this.samples[this.used++] = value / channels.length;
      if (this.used === this.samples.length) this.flush();
    }
    // Outputs remain silent: never play the user's microphone back.
    return true;
  }
}
registerProcessor("notion-pcm-capture", NotionPcmCapture);

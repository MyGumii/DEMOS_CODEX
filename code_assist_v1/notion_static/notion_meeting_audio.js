(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  root.NotionMeetingAudio = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";
  function wav(samples, sampleRate) {
    const bytes = new ArrayBuffer(44 + samples.length * 2);
    const view = new DataView(bytes);
    const ascii = (at, text) => [...text].forEach((ch, i) => view.setUint8(at + i, ch.charCodeAt(0)));
    ascii(0, "RIFF"); view.setUint32(4, bytes.byteLength - 8, true); ascii(8, "WAVEfmt ");
    view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
    view.setUint32(24, sampleRate, true); view.setUint32(28, sampleRate * 2, true);
    view.setUint16(32, 2, true); view.setUint16(34, 16, true); ascii(36, "data");
    view.setUint32(40, samples.length * 2, true);
    for (let i = 0; i < samples.length; i++) {
      const value = Math.max(-1, Math.min(1, Number.isFinite(samples[i]) ? samples[i] : 0));
      view.setInt16(44 + i * 2, Math.round(value * (value < 0 ? 32768 : 32767)), true);
    }
    return new Blob([bytes], { type: "audio/wav" });
  }

  function capture(source, onChunk, onError, seconds = 12) {
    let context, worklet, ended, stopPromise, stopped = false;
    const streams = [], inputs = [];
    const watch = stream => {
      streams.push(stream);
      stream.getTracks().forEach(track => track.addEventListener("ended", () => { if (!stopped) onError(new Error("소리 공유 또는 마이크 연결이 종료되었습니다.")); }));
    };
    const release = async () => {
      streams.forEach(stream => stream.getTracks().forEach(track => track.stop()));
      inputs.forEach(input => input.disconnect()); worklet?.disconnect();
      if (context && context.state !== "closed") await context.close();
    };
    return {
      async start() {
        if (!globalThis.isSecureContext || !navigator.mediaDevices) throw new Error("오디오 수집은 HTTPS 또는 localhost에서 사용할 수 있습니다.");
        try {
          if (source !== "microphone") {
            if (!navigator.mediaDevices.getDisplayMedia) throw new Error("이 브라우저는 시스템·탭 소리 공유를 지원하지 않습니다. 지원 브라우저에서 열거나 마이크 입력을 선택하세요.");
            // Must run directly from the user's click, before any persistence/network await.
            const display = await navigator.mediaDevices.getDisplayMedia({ video: true, audio: true, systemAudio: "include", selfBrowserSurface: "exclude", surfaceSwitching: "exclude" });
            watch(display);
            if (!display.getAudioTracks().length) throw new Error("공유된 오디오가 없습니다. 탭/전체 화면의 ‘오디오 공유’를 체크하세요. 창 공유는 소리를 지원하지 않을 수 있습니다.");
          }
          if (stopped) { await release(); return; }
          if (source !== "system") {
            watch(await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true }, video: false }));
          }
          if (stopped) { await release(); return; }
          if (streams.some(stream => stream.getTracks().some(track => track.readyState === "ended"))) throw new Error("선택한 오디오 공유가 이미 종료되었습니다. 다시 시작하세요.");
          context = new AudioContext({ sampleRate: 16000 });
          await context.audioWorklet.addModule("/notion_static/notion_audio_worklet.js?v=20260911c");
          if (stopped) { await release(); return; }
          worklet = new AudioWorkletNode(context, "notion-pcm-capture", { processorOptions: { seconds }, channelCount: 1 });
          worklet.port.onmessage = ({ data }) => {
            if (data.samples) onChunk(wav(data.samples, data.sampleRate), data.samples.length / data.sampleRate);
            if (data.stopped) ended?.();
          };
          worklet.onprocessorerror = () => onError(new Error("음성 처리기가 중단되었습니다. 오디오 수집을 다시 시작하세요."));
          streams.forEach(stream => {
            // MediaStreamAudioSource reads audio only; video is never encoded, uploaded or saved.
            const input = context.createMediaStreamSource(stream);
            inputs.push(input);
            if (streams.length > 1) { const gain = context.createGain(); gain.gain.value = 0.5; inputs.push(gain); input.connect(gain); gain.connect(worklet); }
            else input.connect(worklet);
          });
          worklet.connect(context.destination); // Worklet outputs silence, never plays the captured sound back.
          await context.resume();
        } catch (error) { await release(); throw error; }
      },
      pause() { streams.forEach(stream => stream.getAudioTracks().forEach(track => { track.enabled = false; })); worklet?.port.postMessage("pause"); },
      resume() { worklet?.port.postMessage("resume"); streams.forEach(stream => stream.getAudioTracks().forEach(track => { track.enabled = true; })); },
      stop() {
        if (stopPromise) return stopPromise;
        stopped = true;
        stopPromise = (async () => {
          // The worklet acknowledges only after delivering the last partial PCM buffer.
          if (worklet && context.state === "running") await new Promise(resolve => {
            const timeout = setTimeout(() => { onError(new Error("마지막 음성 구간을 확인하지 못했습니다.")); resolve(); }, 1500);
            ended = () => { clearTimeout(timeout); resolve(); };
            worklet.port.postMessage("stop");
          });
          await release();
        })();
        return stopPromise;
      },
    };
  }

  function microphone(onChunk, onError, seconds) { return capture("microphone", onChunk, onError, seconds); }
  function sourceFactory(source) { return (onChunk, onError, seconds) => capture(["system", "mixed", "microphone"].includes(source) ? source : "system", onChunk, onError, seconds); }

  class Meeting {
    constructor(options) {
      this.options = options;
      this.phase = "idle";
      this.queue = [];
      this.sequence = 0;
      this.duration = 0;
      this.received = 0;
      this.message = "";
      this.capture = null;
      this.draining = null;
      this.disposed = false;
      this.recording = false;
      this.startedAt = null;
      this.controller = new AbortController();
      this.stopPromise = null;
      this.identity = globalThis.crypto?.randomUUID?.() || `meeting-${Date.now()}-${Math.random()}`;
    }
    notify(message) {
      if (message !== undefined) this.message = message;
      this.options.onState?.(this);
    }
    async start() {
      if (this.phase !== "idle" || this.disposed) return;
      this.phase = "starting"; this.notify("오디오 공유·입력 권한을 확인하고 있습니다…");
      this.capture = (this.options.captureFactory || microphone)(
        (blob, duration) => this.enqueue(blob, duration), error => this.fail(error), this.options.chunkSeconds || 12);
      try {
        await this.capture.start();
        if (this.disposed || this.phase !== "starting") { await this.capture.stop(); return; }
        this.startedAt = new Date().toISOString();
        this.recording = true; this.phase = "recording";
        this.notify("녹음 중 · 음성을 순서대로 전사합니다.");
      } catch (error) {
        const message = error.name === "NotAllowedError" ? "오디오 공유가 취소되었거나 권한이 거부되었습니다. 다시 시작해 입력을 선택하세요." : error.message;
        this.fail(new Error(message));
      }
    }
    enqueue(blob, duration = 0) {
      if (this.disposed) return;
      const item = { id: `${this.identity}-${++this.sequence}`, blob, duration, offset: this.duration, applied: false, result: null };
      this.duration += duration;
      this.queue.push(item);
      if (this.queue.length >= 10) { this.fail(new Error("전사가 지연되어 녹음을 멈췄습니다. 대기 음성을 재시도하세요.")); return; }
      this.notify(); this.drain();
    }
    async drain() {
      if (this.draining) return this.draining;
      if (this.phase === "error" || this.disposed) return;
      this.draining = (async () => {
        while (this.queue.length && !this.disposed && this.phase !== "error") {
          const item = this.queue[0];
          try {
            if (!this.options.valid()) throw new Error("페이지 또는 편집 권한이 변경되어 음성 처리를 중단했습니다.");
            if (!item.result) item.result = await this.options.transcribe(item, this.controller.signal);
            if (this.disposed) break;
            if (!this.options.valid()) throw new Error("페이지 또는 편집 권한이 변경되었습니다.");
            if (!item.applied) {
              this.options.onText(item.result.text || "", item);
              item.applied = true;
            }
            if (!(await this.options.persist())) throw new Error("전사 원문을 저장하지 못했습니다. 저장 상태를 확인하고 재시도하세요.");
            if (this.disposed) break;
            this.queue.shift(); this.received++;
            this.options.onCommitted?.(item);
            if (this.phase !== "error") this.notify(this.recording ? "녹음 중 · 전사 원문 저장됨" : "전사 원문 저장됨");
          } catch (error) { this.fail(error); }
        }
      })();
      try { await this.draining; }
      finally { this.draining = null; }
    }
    pause() {
      if (this.phase !== "recording") return;
      this.phase = "paused"; this.recording = false; this.capture.pause();
      this.notify("일시정지 · 새 음성 수집 중단 (이전 구간 전사는 계속됩니다)");
    }
    resume() {
      if (this.phase !== "paused") return;
      this.phase = "recording"; this.recording = true; this.capture.resume(); this.notify("녹음 중");
    }
    fail(error) {
      if (this.disposed) return;
      this.phase = "error"; this.recording = false;
      this.notify(error.message || "음성 인식에 실패했습니다.");
      this.capture?.stop().catch(() => {});
    }
    async retry() {
      if (this.phase !== "error" || !this.queue.length) return;
      await this.capture?.stop();
      this.phase = "stopping"; this.notify("대기 음성을 재시도합니다…");
      await this.drain();
      if (!this.queue.length && this.phase !== "error") await this.finish();
    }
    async finish() {
      if (this.disposed || this.phase === "stopped") return;
      this.phase = "stopped"; this.notify("전사 완료");
      await this.options.onStopped?.();
    }
    stop() {
      if (this.stopPromise) return this.stopPromise;
      if (["stopped", "error"].includes(this.phase) || this.disposed) return Promise.resolve();
      this.stopPromise = (async () => {
        this.phase = "stopping"; this.recording = false; this.notify("녹음 종료 · 남은 음성 전사 중…");
        await this.capture?.stop();
        await this.drain();
        if (!this.disposed && this.phase !== "error" && !this.queue.length) await this.finish();
      })();
      return this.stopPromise;
    }
    async dispose() {
      this.disposed = true; this.recording = false;
      this.controller.abort(); this.phase = "stopped";
      await this.capture?.stop();
      this.queue.length = 0;
      this.notify();
    }
  }
  return Object.freeze({ wav, microphone, sourceFactory, Meeting });
});

(function(root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.NotionEmbeddedEditorBridge = api;
})(typeof globalThis === "object" ? globalThis : this, function() {
  "use strict";
  const clone = value => JSON.parse(JSON.stringify(value));
  const pause = milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds));
  function createChild(host, options = {}) {
    const wait = options.wait || pause, now = options.now || Date.now;
    let parent = null, initialApplied = false, saveFlight = null, beforeLeaveFlight = null;
    const timeoutError = () => new Error("저장이 아직 완료되지 않았습니다. 편집 내용을 유지한 채 다시 시도해 주세요.");
    const until = async (promise, deadline) => {
      const remaining = deadline - now();
      if (remaining <= 0) throw timeoutError();
      let timer;
      try {
        return await Promise.race([promise, new Promise((_, reject) => {
          timer = setTimeout(() => reject(timeoutError()), remaining);
        })]);
      } finally { clearTimeout(timer); }
    };
    const flight = (current, set, work) => {
      if (current) return current;
      const pending = Promise.resolve().then(work);
      set(pending);
      // A timed-out caller does not cancel a save already accepted by the
      // child. Keep it single-flight until it settles, including on failure.
      void pending.then(() => set(null), () => set(null));
      return pending;
    };
    return {
      state: () => host.state(),
      snapshot: () => clone(host.snapshot()),
      async refreshAfterMove(expected) {
        const state = host.state();
        if (state.userId !== expected.userId || state.workspaceId !== expected.workspaceId || state.pageId !== expected.pageId) {
          throw new Error("이동 후 갱신할 편집기와 계정이 달라졌습니다.");
        }
        if (typeof host.refreshAfterMove !== "function") throw new Error("편집기를 새로고침한 뒤 다시 시도해 주세요.");
        return host.refreshAfterMove(expected.workspaceEpoch);
      },
      bindParent(callbacks) { parent = callbacks; },
      navigate(pageId) { return parent?.navigate(pageId); },
      close() { return parent?.close(); },
      focus(selection) { host.focus(selection); },
      renameTitle(title, expected) {
        const state = host.state();
        if (!state.ready || !state.canEdit || state.blocked || state.composing) {
          throw new Error(state.message || "편집 준비와 저장 상태를 확인한 뒤 다시 시도해 주세요.");
        }
        if (state.pageId !== expected.pageId
            || (expected.userId && state.userId !== expected.userId)
            || (expected.workspaceId && state.workspaceId !== expected.workspaceId)) {
          throw new Error("이름을 변경할 페이지와 계정이 달라졌습니다.");
        }
        const current = String(host.snapshot().page.title ?? "");
        if (current === title) return false;
        if (current !== expected.title) {
          const error = new Error("다른 편집에서 페이지 이름이 바뀌었습니다. 확인 후 다시 저장해 주세요.");
          error.code = "TITLE_CHANGED"; error.currentTitle = current;
          throw error;
        }
        host.renameTitle(title);
        return true;
      },
      insertChildLink(page, parentId) {
        const state = host.state();
        if (!state.ready || !state.canEdit || state.composing || state.pageId !== parentId) {
          throw new Error("상위 페이지를 편집할 수 있을 때 다시 저장해 주세요.");
        }
        return host.insertChildLink(page, parentId);
      },
      applyInitial(draft, expected) {
        if (initialApplied) throw new Error("초안이 이미 편집기로 전달됐습니다.");
        const state = host.state(), current = host.snapshot();
        if (!state.ready || !state.canEdit || state.composing) throw new Error("편집 준비가 끝난 후 다시 저장해 주세요.");
        if (state.pageId !== expected.pageId || String(current.page.title) !== String(expected.title)
            || (current.blocks || []).some(block => block.type !== "paragraph" || String(block.content || "").trim()
              || Object.keys(block.props || {}).length)) {
          throw new Error("생성된 페이지에 다른 변경이 있습니다. 초안 내용을 유지했습니다.");
        }
        // This synchronous handoff allows the parent to take its latest input
        // and swap surfaces without an intervening input event being dropped.
        host.apply(clone(draft));
        initialApplied = true;
      },
      async flush(timeoutMs = 15000, options = {}) {
        const deadline = now() + timeoutMs;
        if (!options.preserveActivity && host.beforeLeave && !(await until(
          flight(beforeLeaveFlight, value => {beforeLeaveFlight = value;}, () => host.beforeLeave()), deadline)))
          throw new Error("진행 중인 작업을 마친 후 다시 시도해 주세요.");
        do {
          await until(flight(saveFlight, value => {saveFlight = value;}, () => host.save()), deadline);
          const state = host.state();
          if (state.blocked) throw new Error(state.message || "현재 변경사항을 저장할 수 없습니다.");
          if (state.ready && !state.dirty && !state.composing) return clone(host.snapshot());
          if (now() >= deadline) break;
          await wait(100);
        } while (true);
        throw timeoutError();
      },
    };
  }
  async function connect(frame, pageId, callbacks, options = {}) {
    const deadline = Date.now() + (options.timeoutMs || 30000);
    let child;
    do {
      if (!frame.isConnected) throw new Error("옆 보기 편집기가 닫혔습니다.");
      child = frame.contentWindow?.NotionEmbeddedEditor;
      const state = child?.state();
      if (state?.blocked) throw new Error(state.message || "편집기를 열 수 없습니다.");
      if (state?.ready) {
        if (state.pageId !== pageId) throw new Error("다른 페이지의 편집기 응답을 받았습니다.");
        if ((options.workspaceId && state.workspaceId !== options.workspaceId)
            || (options.userId && state.userId !== options.userId)) {
          throw new Error("초안을 연 계정과 워크스페이스에서 다시 연결해 주세요.");
        }
        child.bindParent(callbacks);
        let last = "";
        let disposed = false;
        const timer = setInterval(() => {
          if (disposed || !frame.isConnected) { clearInterval(timer); return; }
          const state = child.state();
          const signature = JSON.stringify(state);
          if (signature !== last) { last = signature; callbacks.change(state); }
        }, 150);
        return {
          state: child.state, snapshot: child.snapshot, applyInitial: child.applyInitial,
          flush: child.flush, focus: child.focus, insertChildLink: child.insertChildLink, renameTitle: child.renameTitle,
          refreshAfterMove: child.refreshAfterMove,
          dispose() { disposed = true; clearInterval(timer); child.bindParent(null); frame.remove(); },
        };
      }
      await pause(100);
    } while (Date.now() < deadline);
    throw new Error("편집기 연결을 완료하지 못했습니다. 초안은 유지됩니다.");
  }
  return {createChild, connect};
});

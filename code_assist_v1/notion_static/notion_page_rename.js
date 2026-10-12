(function(root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.NotionPageRename = api;
})(typeof globalThis === "object" ? globalThis : this, function() {
  "use strict";

  function createSession(context, host) {
    const expected = {...context};
    let editor = null, flight = null, pending = false, requested = expected.title, attached = true;
    let complete = false, error = "", stopWatching = null, saved = null, applyError = null;
    const listeners = new Set();
    const state = () => ({title: requested, pending, busy: Boolean(flight), complete, error, attached});
    function notify() { for (const listener of listeners) listener(state()); }
    function release() { editor?.dispose?.(); editor = null; stopWatching?.(); stopWatching = null; }
    function bound() {
      const value = editor?.state();
      return value && value.pageId === expected.pageId
        && (!expected.userId || value.userId === expected.userId)
        && (!expected.workspaceId || value.workspaceId === expected.workspaceId);
    }
    function finish(data) {
      if (!bound() || data?.page?.id !== expected.pageId) throw new Error("다른 페이지의 저장 응답을 받았습니다.");
      const current = String(data.page.title ?? "");
      if (current !== requested) {
        pending = false; expected.title = current;
        release();
        throw new Error("저장 중 다른 편집에서 이름이 바뀌었습니다. 입력한 이름을 확인하고 다시 저장해 주세요.");
      }
      pending = false; complete = true; error = ""; saved = data;
      if (!attached) release();
      try { host.saved(data); } catch (reason) { host.onError?.(reason); }
      notify();
      return data;
    }
    function observe() {
      if (flight || !pending) return;
      if (!bound()) { release(); return; }
      const value = editor.state();
      if (!value.ready || value.dirty || value.composing || value.blocked) return;
      try { finish(editor.snapshot()); }
      catch (reason) { error = reason.message; notify(); }
    }
    function applyLatest() {
      if (!host.current() || !bound()) throw new Error("페이지를 연 계정과 워크스페이스에서 다시 시도해 주세요.");
      editor.renameTitle(requested, expected);
      expected.title = requested;
      pending = true;
    }
    function save(title) {
      title = host.normalize ? host.normalize(String(title)) : String(title);
      if (title === requested && complete && !error) return Promise.resolve(saved);
      if (title === expected.title && !flight && !pending && !error) return Promise.resolve(saved);
      requested = String(title); error = ""; complete = false; applyError = null;
      // Keep accepting input while a previous save is awaiting its server ACK.
      if (editor && bound()) {
        try { applyLatest(); } catch (reason) { applyError = reason; }
      }
      if (flight) { notify(); return flight; }
      flight = Promise.resolve().then(async () => {
        if (!host.current()) throw new Error("페이지를 연 계정과 워크스페이스에서 다시 시도해 주세요.");
        if (editor && !bound()) release();
        if (!editor) editor = await host.acquire(expected);
        if (!bound()) throw new Error("이름을 변경할 페이지와 계정이 달라졌습니다.");
        if (applyError) throw applyError;
        applyLatest();
        const data = await editor.flush(15000, {preserveActivity:true});
        if (applyError) throw applyError;
        return finish(data);
      }).catch(reason => {
        if (reason.code === "TITLE_CHANGED") expected.title = reason.currentTitle;
        error = reason.message;
        host.failed?.(reason);
        throw reason;
      }).finally(() => {
        flight = null;
        if (!pending && !attached) release();
        else if (pending && !stopWatching && host.watch) stopWatching = host.watch(observe);
        notify();
        observe();
      });
      notify();
      return flight;
    }
    return {
      state, save, observe,
      attach() { attached = true; },
      subscribe(listener) { listeners.add(listener); listener(state()); return () => listeners.delete(listener); },
      dismiss() {
        attached = false;
        if (!pending && !flight) release();
        return !pending && !flight && !error;
      },
    };
  }

  function mount(root, session, options) {
    root.innerHTML = `<div class="page-rename-field"><span class="page-rename-icon" aria-hidden="true">${options.iconHtml}</span><input type="text" aria-label="페이지 이름" placeholder="제목 없음" autocomplete="off" spellcheck="false"></div><div class="page-rename-status" role="status" hidden></div><button class="page-rename-retry" type="button" hidden>다시 저장</button>`;
    const input = root.querySelector("input"), message = root.querySelector("[role=status]"), retry = root.querySelector("button");
    let composing = false, disposed = false, outsideAfterComposition = false;
    input.value = session.state().title;
    function commit() {
      if (composing || disposed) return;
      session.save(input.value).catch(() => {});
    }
    const unsubscribe = session.subscribe(value => {
      if (disposed) return;
      root.setAttribute("aria-busy", String(value.busy || value.pending));
      message.textContent = value.error;
      message.hidden = !message.textContent;
      retry.hidden = !value.error || value.busy;
    });
    input.addEventListener("compositionstart", () => { composing = true; });
    input.addEventListener("compositionend", () => {
      composing = false; commit();
      if (outsideAfterComposition) options.close(false);
    });
    input.addEventListener("input", commit);
    root.addEventListener("keydown", event => {
      if (event.isComposing || composing || event.keyCode === 229) return;
      if (event.key === "Enter" || event.key === "Escape") {
        event.preventDefault(); event.stopPropagation(); options.close(true);
      }
    });
    retry.addEventListener("click", commit);
    input.focus({preventScroll:true}); input.select();
    return {
      dismissFromOutside() {
        if (composing) { outsideAfterComposition = true; return; }
        options.close(false);
      },
      dispose() { disposed = true; unsubscribe(); },
    };
  }
  return {createSession, mount};
});

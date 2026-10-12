/* Equation controls edit plain LaTeX; generated KaTeX is never persisted. */
(function (root) {
  "use strict";
  let active = null;
  function html(source, editable) {
    const preview = root.NotionMath.renderHtml(source);
    return editable
      ? `<button type="button" class="equation-preview equation-trigger" data-equation-trigger aria-label="수식 수정" aria-haspopup="dialog" aria-expanded="false">${preview}</button>`
      : `<div class="equation-preview">${preview}</div>`;
  }
  function bind(row, adapter) {
    const trigger = row.querySelector("[data-equation-trigger]");
    if (!trigger) return;
    const valid = () => trigger.isConnected && adapter.canEdit() && adapter.getBlock()?.type === "equation";
    const open = () => {
      if (!valid()) return;
      if (active?.trigger === trigger) { active.input.focus(); return; }
      active?.close(false);
      const popup = document.createElement("div");
      popup.className = "equation-popover";
      popup.setAttribute("role", "dialog");
      popup.setAttribute("aria-label", "수식 편집");
      popup.innerHTML = `<textarea aria-label="수식(LaTeX)" maxlength="20000" spellcheck="false" placeholder="\\alpha, \\frac{a}{b}"></textarea><button type="button" data-equation-done>완료 <span aria-hidden="true">↵</span></button><span class="equation-input-hint">Shift+Enter 줄바꿈</span>`;
      const input = popup.querySelector("textarea");
      input.value = adapter.getBlock().content || "";
      let composing = false, closed = false;
      function position() {
        const anchor = trigger.getBoundingClientRect();
        popup.style.left = `${Math.max(12, Math.min(window.innerWidth - popup.offsetWidth - 12, anchor.left))}px`;
        const below = anchor.bottom + 4;
        popup.style.top = `${Math.max(8, below + popup.offsetHeight > window.innerHeight - 8 ? anchor.top - popup.offsetHeight - 4 : below)}px`;
      }
      function update() {
        if (!valid()) { close(false); return; }
        if (adapter.getBlock().content !== input.value) adapter.change(input.value);
        trigger.innerHTML = root.NotionMath.renderHtml(input.value);
        input.style.height = "auto";
        input.style.height = `${Math.min(240, Math.max(48, input.scrollHeight))}px`;
        position();
      }
      function close(restoreFocus = true) {
        if (closed) return;
        // A final composition/input event can be delayed until focus leaves.
        if (valid() && adapter.getBlock().content !== input.value) adapter.change(input.value);
        if (composing) { composing = false; adapter.endComposition?.(); }
        closed = true;
        clearInterval(watch);
        document.removeEventListener("pointerdown", outside, true);
        document.removeEventListener("scroll", reposition, true);
        window.removeEventListener("resize", reposition);
        popup.remove(); trigger.setAttribute("aria-expanded", "false");
        if (active?.input === input) active = null;
        // A legitimate rerender can detach the trigger while the same page
        // still has an unsaved draft (especially in side peek).
        if (adapter.canEdit() && adapter.getBlock()?.type === "equation") Promise.resolve(adapter.save()).catch(() => {});
        if (restoreFocus && trigger.isConnected) trigger.focus({ preventScroll: true });
      }
      const outside = event => { if (!popup.contains(event.target) && !trigger.contains(event.target)) close(false); };
      const reposition = () => { if (valid()) position(); else close(false); };
      input.addEventListener("compositionstart", () => { composing = true; adapter.beginComposition?.(); });
      input.addEventListener("compositionend", () => {
        composing = false;
        try { update(); } finally { adapter.endComposition?.(); }
      });
      input.addEventListener("input", event => { if (!composing && !event.isComposing) update(); });
      popup.addEventListener("keydown", event => {
        if (composing || event.isComposing || event.keyCode === 229) return;
        if (event.key === "Escape" || (event.key === "Enter" && !event.shiftKey)) {
          event.preventDefault(); event.stopPropagation(); update(); close();
        } else if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") {
          event.preventDefault(); event.stopPropagation(); update(); adapter.save();
        }
      });
      popup.querySelector("[data-equation-done]").addEventListener("click", () => { update(); close(); });
      popup.addEventListener("focusout", event => {
        if (event.relatedTarget && !popup.contains(event.relatedTarget) && event.relatedTarget !== trigger) close(false);
      });
      document.body.appendChild(popup);
      trigger.setAttribute("aria-expanded", "true");
      document.addEventListener("pointerdown", outside, true);
      document.addEventListener("scroll", reposition, true);
      window.addEventListener("resize", reposition);
      const watch = setInterval(() => { if (!valid()) close(false); }, 250);
      active = { trigger, input, close };
      update(); input.focus(); input.setSelectionRange(input.value.length, input.value.length);
    };
    trigger.addEventListener("click", open);
    trigger.addEventListener("keydown", event => {
      if ((event.ctrlKey || event.metaKey) && event.key === "Enter") { event.preventDefault(); event.stopPropagation(); open(); }
    });
  }
  root.NotionEquationEditor = Object.freeze({ html, bind, close: () => active?.close(false) });
})(typeof window === "undefined" ? globalThis : window);

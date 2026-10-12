(function (root, factory) {
  const api = factory(root);
  if (typeof module === "object" && module.exports) module.exports = api;
  root.NotionTitleEditor = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function (root) {
  "use strict";
  const controls = new Map();
  let observer = null;
  function fit(control) {
    if (!control || control.tagName !== "TEXTAREA" || !control.getClientRects().length) return;
    const width = control.getBoundingClientRect().width;
    if (!width) return;
    if (controls.has(control)) controls.set(control, width);
    // Layout only: value, selection and composition remain native.
    control.style.height = "0px";
    const border = control.offsetHeight - control.clientHeight;
    control.style.height = `${control.scrollHeight + border}px`;
  }
  function watch(control) {
    for (const known of controls.keys()) {
      if (!known.isConnected) { observer?.unobserve(known); controls.delete(known); }
    }
    if (!control || control.tagName !== "TEXTAREA") return;
    if (!observer && root.ResizeObserver) observer = new root.ResizeObserver(entries => {
      for (const {target} of entries) {
        if (!target.isConnected) { observer.unobserve(target); controls.delete(target); continue; }
        if (target.getBoundingClientRect().width !== controls.get(target)) fit(target);
      }
    });
    if (!controls.has(control)) { controls.set(control, 0); observer?.observe(control); }
    fit(control);
  }
  function split(value, start, end = start) {
    const text = String(value ?? "");
    const from = Math.max(0, Math.min(text.length, Number(start) || 0));
    const to = Math.max(from, Math.min(text.length, Number(end) || 0));
    return {title: text.slice(0, from), content: text.slice(to)};
  }
  root.document?.fonts?.ready?.then(() => { for (const control of controls.keys()) fit(control); });
  return Object.freeze({fit, watch, split});
});

/* Page-level heading navigation, derived only from the currently visible page. */
(function () {
  "use strict";
  let navigation, canvas, scrollRoot, observer, resizeObserver;
  let entries = [];
  let signature = "";
  let pending = false;

  function schedule() {
    if (pending) return;
    pending = true;
    requestAnimationFrame(() => { pending = false; refresh(); });
  }

  function initialize() {
    canvas = document.getElementById("blocks");
    scrollRoot = document.getElementById("workspaceMain");
    const pageView = document.getElementById("pageView");
    if (!canvas || !scrollRoot || !pageView) return false;
    navigation = document.createElement("nav");
    navigation.id = "pageOutline";
    navigation.className = "page-outline";
    navigation.setAttribute("aria-label", "페이지 목차");
    navigation.hidden = true;
    pageView.appendChild(navigation);
    navigation.addEventListener("keydown", event => {
      const buttons = [...navigation.querySelectorAll("button")];
      const index = buttons.indexOf(document.activeElement);
      if (index < 0) return;
      const target = { ArrowDown: Math.min(index + 1, buttons.length - 1), ArrowUp: Math.max(0, index - 1), Home: 0, End: buttons.length - 1 }[event.key];
      if (target !== undefined) {
        event.preventDefault();
        buttons[target]?.focus({ preventScroll: true });
      }
    });
    observer = new MutationObserver(schedule);
    observer.observe(canvas, { subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: ["hidden", "data-type", "style"] });
    resizeObserver = new ResizeObserver(schedule);
    resizeObserver.observe(scrollRoot);
    resizeObserver.observe(canvas);
    scrollRoot.addEventListener("scroll", updatePosition, { passive: true });
    window.addEventListener("resize", schedule, { passive: true });
    return true;
  }

  function updatePosition() {
    if (!navigation || navigation.hidden) return;
    const rect = scrollRoot.getBoundingClientRect();
    navigation.style.right = `${Math.max(12, window.innerWidth - rect.right + 16)}px`;
    navigation.style.top = `${Math.max(88, rect.top + 104)}px`;
    let active = entries[0];
    for (const entry of entries) {
      if (entry.row.getBoundingClientRect().top <= Math.max(88, rect.top + 96)) active = entry;
    }
    if (scrollRoot.scrollTop > 0 && scrollRoot.scrollTop + scrollRoot.clientHeight >= scrollRoot.scrollHeight - 3) active = entries.at(-1);
    for (const entry of entries) {
      if (entry === active) entry.button.setAttribute("aria-current", "location");
      else entry.button.removeAttribute("aria-current");
    }
  }

  function refresh() {
    if (!navigation && !initialize()) return;
    const headings = [...canvas.querySelectorAll(".block-row[data-type]")].filter(row => (
      /^(?:toggle_)?heading[123]$/.test(row.dataset.type)
      && row.id && !row.hidden
      && Number(row.style.getPropertyValue("--block-indent") || 0) === 0
      && row.getClientRects().length > 0
      && row.querySelector(".block-editor")?.textContent.trim()
    ));
    navigation.hidden = headings.length < 2 || scrollRoot.clientWidth < 600;
    const next = JSON.stringify(headings.map(row => [row.id, row.dataset.type, row.querySelector(".block-editor").textContent.trim()]));
    if (next !== signature || entries.some((entry, index) => entry.row !== headings[index])) {
      signature = next;
      navigation.replaceChildren();
      entries = headings.map(row => {
        const title = row.querySelector(".block-editor").textContent.trim();
        const button = document.createElement("button");
        button.type = "button";
        button.setAttribute("aria-label", title);
        button.setAttribute("aria-controls", row.id);
        button.style.setProperty("--outline-level", row.dataset.type.slice(-1));
        const bar = document.createElement("span");
        bar.className = "page-outline-bar";
        bar.setAttribute("aria-hidden", "true");
        const label = document.createElement("span");
        label.className = "page-outline-label";
        label.textContent = title;
        button.append(bar, label);
        button.addEventListener("click", event => {
          const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
          const offset = row.getBoundingClientRect().top - scrollRoot.getBoundingClientRect().top + scrollRoot.scrollTop - 72;
          scrollRoot.scrollTo({ top: Math.max(0, offset), behavior: reduced ? "instant" : "smooth" });
          // Mouse navigation must not keep :focus-within expanded after hover
          // ends. Keyboard activation retains focus for arrow-key navigation.
          if (event.detail > 0) button.blur();
        });
        navigation.appendChild(button);
        return { row, button };
      });
    }
    updatePosition();
  }

  window.NotionPageOutline = Object.freeze({ refresh });
})();

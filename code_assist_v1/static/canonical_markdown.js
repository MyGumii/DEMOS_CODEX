/* Read-only Wiki display. Stored Markdown never becomes active page HTML. */
(function (root, factory) {
  const api = factory(root);
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.CanonicalMarkdown = api;
})(typeof globalThis === "object" ? globalThis : this, function (root) {
  "use strict";
  const allowed = new Set("p br hr h1 h2 h3 h4 h5 h6 strong b em i s del code pre blockquote ul ol li table thead tbody tfoot tr th td caption a span div sub sup".split(" "));
  const omitted = new Set("script style iframe object embed svg math form input button textarea select option link meta base template".split(" "));

  function safeHref(value) {
    if (typeof value !== "string" || /[\u0000-\u0020\u007f\\]/.test(value)) return null;
    return /^(?:https?:\/\/|\/(?!\/)|#)/i.test(value) ? value : null;
  }

  function appendSafe(documentRef, source, target) {
    for (const child of source.childNodes) {
      if (child.nodeType === 3) {
        target.appendChild(documentRef.createTextNode(child.textContent));
        continue;
      }
      if (child.nodeType !== 1) continue;
      const tag = child.localName.toLowerCase();
      if (omitted.has(tag)) continue;
      if (tag === "img") {
        // Do not fetch arbitrary remote URLs, tracking images, or local files.
        // Original files remain available through the authorized download UI.
        target.appendChild(documentRef.createTextNode(`[이미지: ${child.getAttribute("alt") || "원본 파일 참조"}]`));
        continue;
      }
      if (!allowed.has(tag)) {
        appendSafe(documentRef, child, target);
        continue;
      }
      const clean = documentRef.createElement(tag);
      if (tag === "a") {
        const href = safeHref(child.getAttribute("href"));
        if (href) {
          clean.setAttribute("href", href);
          clean.setAttribute("rel", "noopener noreferrer");
        }
      }
      if (tag === "td" || tag === "th") {
        for (const name of ["colspan", "rowspan"]) {
          const value = child.getAttribute(name);
          if (/^[1-9][0-9]{0,2}$/.test(value || "")) clean.setAttribute(name, value);
        }
      }
      appendSafe(documentRef, child, clean);
      target.appendChild(clean);
    }
  }

  function render(documentRef, markdown, htmlRenderer = root && root.renderMd) {
    const body = documentRef.createElement("div");
    body.className = "item canonical-item-markdown";
    if (markdown === null) {
      body.textContent = "본문을 사용할 수 없습니다.";
      body.dataset.markdownDisplay = "unavailable";
      return body;
    }
    const source = String(markdown);
    try {
      if (typeof htmlRenderer !== "function") throw new TypeError("Markdown renderer unavailable");
      const math = root && root.CanonicalMath;
      const prepared = math && typeof math.prepare === "function" && typeof math.hydrate === "function"
        ? math.prepare(source) : null;
      const html = htmlRenderer(prepared ? prepared.markdown : source,
        prepared ? { preserveMath: true } : undefined);
      if (typeof html !== "string") throw new TypeError("Markdown result is not text");
      // template content is inert and never mounted. Only allowlisted nodes
      // freshly created below enter the live DOM, without event/style attributes.
      const template = documentRef.createElement("template");
      template.innerHTML = html;
      appendSafe(documentRef, template.content, body);
      if (prepared) math.hydrate(documentRef, body, prepared);
      body.dataset.markdownDisplay = "rendered";
    } catch (_) {
      body.replaceChildren();
      const notice = documentRef.createElement("p");
      notice.textContent = "서식을 표시하지 못해 Markdown 원문을 보여드립니다.";
      const raw = documentRef.createElement("pre");
      raw.textContent = source;
      body.append(notice, raw);
      body.dataset.markdownDisplay = "source";
    }
    return body;
  }
  return Object.freeze({ render });
});

/* Equation source remains plain LaTeX; generated markup is display-only. */
(function (root) {
  "use strict";
  const escape = value => String(value).replace(/[&<>"']/g, char => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[char]));

  function normalizeSource(value) {
    const source = String(value || "").trim();
    for (const [start, end] of [["$$", "$$"], ["\\[", "\\]"], ["\\(", "\\)"], ["$", "$"]]) {
      if (source.length >= start.length + end.length && source.startsWith(start) && source.endsWith(end)) {
        return source.slice(start.length, -end.length).trim();
      }
    }
    return source;
  }

  function renderHtml(value) {
    const source = normalizeSource(value);
    if (!source) return '<span class="equation-placeholder">수식을 입력하세요</span>';
    try {
      if (!root.katex) throw new Error("수식 렌더러를 불러오지 못했습니다. 페이지를 새로고침해 주세요.");
      if (source.length > 20000) throw new Error("수식이 너무 깁니다. 여러 수식 블록으로 나눠 주세요.");
      return root.katex.renderToString(source, {
        displayMode: true,
        output: "htmlAndMathml",
        throwOnError: true,
        trust: false,
        strict: "ignore",
        maxExpand: 1000,
        maxSize: 20,
        macros: {},
      });
    } catch (error) {
      return `<span class="equation-error" role="status">수식을 확인해 주세요: ${escape(error.message)}</span>`;
    }
  }

  root.NotionMath = Object.freeze({ normalizeSource, renderHtml });
})(typeof window === "undefined" ? globalThis : window);

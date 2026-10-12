/* Display-only math: source stays unchanged; untrusted HTML stays forbidden. */
(function (root, factory) {
  const api = factory(root);
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.CanonicalMath = api;
})(typeof globalThis === "object" ? globalThis : this, function (root) {
  "use strict";
  let sequence = 0;

  function escaped(source, offset) {
    let count = 0;
    while (offset > 0 && source[--offset] === "\\") count++;
    return count % 2 === 1;
  }

  function nextAt(indices, start) {
    let low = 0, high = indices.length;
    while (low < high) {
      const middle = (low + high) >>> 1;
      if (indices[middle] < start) low = middle + 1; else high = middle;
    }
    return low < indices.length ? indices[low] : -1;
  }

  function literalEnd(source, offset, indentedCode) {
    const lineStart = offset === 0 || source[offset - 1] === "\n";
    const fence = lineStart && /^( {0,3})(`{3,}|~{3,})[^\n]*(?:\n|$)/.exec(source.slice(offset));
    if (fence) {
      const pattern = new RegExp(`^ {0,3}${fence[2][0]}{${fence[2].length},}[ \\t]*(?:\\n|$)`, "gm");
      pattern.lastIndex = offset + fence[0].length;
      const end = pattern.exec(source);
      return end ? end.index + end[0].length : source.length;
    }
    if (lineStart && indentedCode) {
      const end = source.indexOf("\n", offset);
      return end < 0 ? source.length : end + 1;
    }
    if (source[offset] === "`" && !escaped(source, offset)) {
      const ticks = /^`+/.exec(source.slice(offset))[0];
      let end = source.indexOf(ticks, offset + ticks.length);
      while (end >= 0) {
        if (source[end - 1] !== "`" && source[end + ticks.length] !== "`") return end + ticks.length;
        end = source.indexOf(ticks, end + ticks.length);
      }
    }
    const reference = lineStart && /^ {0,3}\[[^\]\n]+\]:[^\n]*(?:\n|$)/.exec(source.slice(offset));
    if (reference) return offset + reference[0].length;
    if ((offset === 0 || !/[A-Za-z0-9_]/.test(source[offset - 1])) &&
        (source.startsWith("https://", offset) || source.startsWith("http://", offset) || source.startsWith("www.", offset))) {
      const url = /^(?:https?:\/\/|www\.)[^\s<>]+/.exec(source.slice(offset));
      if (url) return offset + url[0].length;
    }
    if (source[offset] === "<") {
      const codeTag = /^<(pre|code)(?:\s[^>]*|)>/i.exec(source.slice(offset));
      if (codeTag) {
        const endTag = new RegExp(`</${codeTag[1]}\\s*>`, "gi");
        endTag.lastIndex = offset + codeTag[0].length;
        const end = endTag.exec(source);
        return end ? end.index + end[0].length : source.length;
      }
      const tag = /^<(?:!--[\s\S]*?--|\/?[A-Za-z][A-Za-z0-9:-]*(?:[^>"']|"[^"]*"|'[^']*')*|(?:https?:\/\/|mailto:)[^<>\n]*)>/.exec(source.slice(offset));
      if (tag) return offset + tag[0].length;
    }
    if (source.startsWith("](", offset) && !escaped(source, offset)) {
      let last = offset + 2, depth = 1, quote = null;
      for (; last < source.length; last++) {
        if (escaped(source, last)) continue;
        const char = source[last];
        if (quote) { if (char === quote) quote = null; continue; }
        if ((char === '"' || char === "'") && /\s/.test(source[last - 1])) { quote = char; continue; }
        if (char === "(") depth++;
        else if (char === ")" && --depth === 0) return last + 1;
      }
      return last;
    }
    return offset;
  }

  function scanIndex(source) {
    const literals = new Map(), barriers = [], blocked = new Uint8Array(source.length);
    const listPadding = [];
    for (let offset = 0; offset < source.length;) {
      let indentedCode = false;
      if (offset === 0 || source[offset - 1] === "\n") {
        const lineEnd = source.indexOf("\n", offset);
        const line = source.slice(offset, lineEnd < 0 ? source.length : lineEnd);
        const leading = /^[ \t]*/.exec(line)[0];
        const indent = [...leading].reduce((column, char) => char === "\t" ? column + 4 - column % 4 : column + 1, 0);
        const marker = /^([-+*]|\d{1,9}[.)])([ \t]+)/.exec(line.slice(leading.length));
        if (line.trim()) {
          while (listPadding.length && indent < listPadding[listPadding.length - 1]) listPadding.pop();
          if (marker) {
            // List continuation indentation is not an indented code block.
            const padding = marker[2].length > 4 ? 1 : marker[2].length;
            listPadding.push(indent + marker[1].length + padding);
          } else indentedCode = indent >= (listPadding.length ? listPadding[listPadding.length - 1] : 0) + 4;
        }
      }
      const end = literalEnd(source, offset, indentedCode);
      if (end > offset) {
        // HTML-like comparisons and URL arguments inside an equation are
        // TeX data. They protect openers in ordinary prose/attributes but do
        // not hide closing math delimiters (e.g. \includegraphics{URL}$$).
        const softTag = source[offset] === "<" && !/^<(?:pre\b|code\b|!--)/i.test(source.slice(offset));
        const softUrl = /^(?:https?:\/\/|www\.)/.test(source.slice(offset, offset + 8));
        if (!softTag && !softUrl) { barriers.push(offset); blocked.fill(1, offset, end); }
        literals.set(offset, end); offset = end;
      } else offset++;
    }
    const newlines = [], closes = new Map([
      ["$", []], ["$$", []], ["\\]", []], ["\\)", []],
    ]);
    let slashes = 0;
    for (let offset = 0; offset < source.length; offset++) {
      const isEscaped = slashes % 2 === 1;
      slashes = source[offset] === "\\" ? slashes + 1 : 0;
      if (source[offset] === "\n") newlines.push(offset);
      if (blocked[offset] || isEscaped) continue;
      for (const [delimiter, positions] of closes) {
        if (!source.startsWith(delimiter, offset)) continue;
        if (delimiter === "$" && (source[offset - 1] === "$" || source[offset + 1] === "$")) continue;
        positions.push(offset);
      }
    }
    return { literals, barriers, newlines, closes };
  }

  function prepare(value) {
    const source = String(value);
    // Numeric HTML entities can produce marker letters after Markdown parsing.
    // This decoded view is used ONLY to choose a noncolliding inert marker.
    const decodedView = source.replace(/&#(x[0-9a-f]+|[0-9]+);?/gi, (entity, number) => {
      const value = number[0].toLowerCase() === "x" ? parseInt(number.slice(1), 16) : Number(number);
      return value > 0 && value <= 0x10ffff ? String.fromCodePoint(value) : entity;
    });
    let prefix;
    // A marker absent from the entire source cannot be mistaken for literal prose.
    do { prefix = `SBMathSlot${++sequence}Q`; } while (source.includes(prefix) || decodedView.includes(prefix));
    const index = scanIndex(source);
    const slots = [];
    let result = "", offset = 0;
    while (offset < source.length) {
      // Both openers and closing lookahead respect code/URL/HTML boundaries.
      const literalLast = index.literals.get(offset);
      if (literalLast !== undefined) {
        result += source.slice(offset, literalLast); offset = literalLast;
        continue;
      }
      let open = null, close = null, display = false;
      if (!escaped(source, offset)) {
        if (source.startsWith("$$", offset)) { open = close = "$$"; display = true; }
        else if (source.startsWith("\\[", offset)) { open = "\\["; close = "\\]"; display = true; }
        else if (source.startsWith("\\(", offset)) { open = "\\("; close = "\\)"; }
        else if (source[offset] === "$" && source[offset + 1] !== "$" && !/\s/.test(source[offset + 1] || " ")) {
          open = close = "$";
        }
      }
      if (open) {
        const start = offset + open.length;
        let end = nextAt(index.closes.get(close), start);
        const barrier = nextAt(index.barriers, start);
        const newline = display ? -1 : nextAt(index.newlines, start);
        if ((barrier >= 0 && barrier < end) || (newline >= 0 && newline < end)) end = -1;
        const inner = end < 0 ? "" : source.slice(offset + open.length, end);
        // Avoid treating ordinary paired currency amounts as inline equations.
        const currency = open === "$" && /^\d/.test(inner) && !/[\\_^=<>≤≥+*/{}]/.test(inner);
        if (end >= 0 && inner.trim() && !currency) {
          const marker = `${prefix}${slots.length}End`;
          slots.push(Object.freeze({ marker, source: inner, literal: source.slice(offset, end + close.length), display }));
          result += marker;
          offset = end + close.length;
          continue;
        }
      }
      result += source[offset++];
    }
    return Object.freeze({ markdown: result, slots: Object.freeze(slots), prefix });
  }

  function decodedMath(documentRef, source) {
    // Decode only data inside a math slot, never arbitrary Markdown/HTML.
    return source.replace(/&(?:amp|lt|gt|quot|apos|#\d+|#x[0-9a-f]+);/gi, entity => {
      const node = documentRef.createElement("textarea");
      node.innerHTML = entity;
      return node.value;
    });
  }

  function mathNode(documentRef, slot) {
    const element = documentRef.createElement("span");
    element.className = slot.display ? "canonical-math-display" : "canonical-math-inline";
    try {
      if (!root.katex || typeof root.katex.renderToString !== "function" || slot.source.length > 20000) throw new Error("unavailable");
      const html = root.katex.renderToString(decodedMath(documentRef, slot.source), {
        displayMode: slot.display, output: "htmlAndMathml", throwOnError: true,
        trust: false, strict: "ignore", maxExpand: 1000, maxSize: 20, macros: {},
      });
      // Only output from the locally bundled trusted renderer enters this slot.
      // Document-authored classes, styles, MathML, SVG and HTML remain forbidden.
      const template = documentRef.createElement("template");
      template.innerHTML = html;
      element.appendChild(template.content);
      element.dataset.mathDisplay = "rendered";
    } catch (_) {
      element.textContent = slot.literal;
      element.dataset.mathDisplay = "source";
      element.title = "수식을 표시하지 못해 LaTeX 원문을 보여드립니다.";
    }
    return element;
  }

  function hydrate(documentRef, body, prepared) {
    const slots = new Map(prepared.slots.map(slot => [slot.marker, slot]));
    const expression = new RegExp(`${prepared.prefix}\\d+End`, "g");
    let rendered = 0, fallback = 0;
    function visit(parent, code = false) {
      for (const child of [...parent.childNodes]) {
        if (child.nodeType === 1) {
          visit(child, code || child.localName === "code" || child.localName === "pre");
          continue;
        }
        if (child.nodeType !== 3 || !child.textContent.includes(prepared.prefix)) continue;
        const text = child.textContent;
        expression.lastIndex = 0;
        const fragment = documentRef.createDocumentFragment();
        let last = 0, match;
        while ((match = expression.exec(text))) {
          const slot = slots.get(match[0]);
          if (!slot) continue;
          fragment.appendChild(documentRef.createTextNode(text.slice(last, match.index)));
          if (code) fragment.appendChild(documentRef.createTextNode(slot.literal));
          else {
            const math = mathNode(documentRef, slot);
            fragment.appendChild(math);
            if (math.dataset.mathDisplay === "rendered") rendered++; else fallback++;
          }
          last = match.index + match[0].length;
        }
        if (!last) continue;
        fragment.appendChild(documentRef.createTextNode(text.slice(last)));
        child.replaceWith(fragment);
      }
    }
    visit(body);
    body.dataset.mathRendered = String(rendered);
    body.dataset.mathFallback = String(fallback);
    return Object.freeze({ rendered, fallback });
  }
  return Object.freeze({ prepare, hydrate });
});

/* AI answer Markdown and math. Raw model text never becomes HTML or links. */
(function (root) {
  "use strict";
  const cache = new Map();
  let cacheSize = 0;
  const asciiMathNames = Object.freeze({alpha: "\\alpha", beta: "\\beta", gamma: "\\gamma", delta: "\\delta",
    sigma: "\\sigma", theta: "\\theta", lambda: "\\lambda", mu: "\\mu", pi: "\\pi",
    sum: "\\sum", exp: "\\exp", log: "\\log", tanh: "\\tanh", softmax: "\\operatorname{softmax}",
    LeakyReLU: "\\operatorname{LeakyReLU}", ReLU: "\\operatorname{ReLU}"});
  function node(tag, text, className) {
    const el = root.document.createElement(tag);
    if (text !== undefined) el.textContent = text;
    if (className) el.className = className;
    return el;
  }
  function escaped(text, pos) {
    let slashes = 0;
    while (pos > 0 && text[--pos] === "\\") slashes++;
    return slashes % 2 === 1;
  }
  function endOf(text, delimiter, start) {
    let end = text.indexOf(delimiter, start);
    while (end !== -1 && escaped(text, end)) end = text.indexOf(delimiter, end + delimiter.length);
    return end;
  }
  function repairTex(source) {
    return source.replace(/\u0008(?=(?:eta|egin|ig|igg)(?![A-Za-z]))/g, "\\b")
      .replace(/\u000c(?=rac(?![A-Za-z]))/g, "\\f")
      .replace(/\t(?=(?:ext|heta|imes|au)(?![A-Za-z]))/g, "\\t")
      .replace(/\r(?=ight(?![A-Za-z]))/g, "\\r")
      .replace(/\n(?=(?:abla|u|eq)(?![A-Za-z]))/g, "\\n");
  }
  function repairDelimited(value) {
    return value.replace(/```[\s\S]*?(?:```|$)|`[^`]*`|\$\$[\s\S]*?\$\$|(?<![\\$])\$(?!\$)[\s\S]*?(?<!\\)\$|\\\([\s\S]*?\\\)|\\\[[\s\S]*?\\\]/g,
      part => part.startsWith("`") ? part : repairTex(part));
  }
  function math(source, raw, display) {
    source = repairTex(source);
    const el = node(display ? "div" : "span", undefined, display ? "ai-home-math-block" : "ai-home-math-inline");
    try {
      if (!root.katex || source.length > 16000) throw new Error("math renderer unavailable");
      const key = (display ? "block:" : "inline:") + source;
      let html = cache.get(key);
      if (!html) {
        html = root.katex.renderToString(source, {displayMode: display, output: "htmlAndMathml",
          throwOnError: true, trust: false, strict: "ignore", maxExpand: 1000, maxSize: 20, macros: {}});
        // Closed formulas recur on every streaming repaint; reuse their markup.
        if (html.length <= 1048576) {
          while (cache.size && (cache.size >= 128 || cacheSize + html.length > 1048576)) {
            const first = cache.keys().next().value;
            cacheSize -= cache.get(first).length; cache.delete(first);
          }
          cache.set(key, html); cacheSize += html.length;
        }
      }
      el.innerHTML = html; // Only locally generated, trust:false KaTeX markup.
      el.dataset.mathState = "rendered";
    } catch (_) {
      el.textContent = raw; // Incomplete/unsupported TeX stays readable during generation.
      el.dataset.mathState = "source";
    }
    return el;
  }
  // Older artifacts contain complete equations without Markdown math delimiters.
  // Recognize a small math vocabulary after an indexed-variable assignment;
  // ordinary prose, code, filenames and identifiers stay literal.
  function legacyEquation(value, pos) {
    if (pos && /[\w/\\.$]/.test(value[pos - 1])) return null;
    const head = /^(?:alpha|beta|gamma|delta|sigma|theta|lambda|mu|pi|[A-Za-z])'?_(?:\{[A-Za-z0-9]+\}|[A-Za-z0-9]+)\s*=\s*/.exec(value.slice(pos));
    if (!head) return null;
    const token = /(?:LeakyReLU|softmax|alpha|beta|gamma|delta|sigma|theta|lambda|tanh|ReLU|sum|exp|log|mu|pi|[A-Za-z])(?:')?(?:_(?:\{[A-Za-z0-9]+\}|[A-Za-z0-9]+))?(?:\^(?:\{[A-Za-z0-9]+\}|[A-Za-z0-9]+))?(?![A-Za-z0-9_])|\d+(?:\.\d+)?|\|\||[+*/^=(),;[\]{}-]|[ \t]+/y;
    let end = pos + head[0].length, source = head[0].replace(/^[A-Za-z]+/, name => asciiMathNames[name] || name),
      operands = 0, stack = [], last = "";
    while (end - pos < 2000 && end < value.length) {
      token.lastIndex = end;
      const match = token.exec(value);
      if (!match) break;
      let part = match[0];
      if ((part === "," || part === ";") && !stack.length) break;
      if (part.length === 1 && "([{".includes(part)) stack.push(part);
      if (part.length === 1 && ")]}".includes(part)) {
        const pairs = {"(": ")", "[": "]", "{": "}"};
        if (!stack.length) break;
        if (pairs[stack.pop()] !== part) return null;
      }
      if (/^[A-Za-z\d]/.test(part)) {
        operands++;
        part = part.replace(/^[A-Za-z]+/, name => asciiMathNames[name] || name)
          .replace(/_([A-Za-z0-9]+)/g, "_{$1}").replace(/\^([A-Za-z0-9]+)/g, "^{$1}");
      } else if (part === "||") part = "\\parallel";
      source += part; end = token.lastIndex;
      if (part.trim()) last = match[0];
    }
    if (!operands || stack.length || /^[+*/^=,;-]$/.test(last)) return null;
    const raw = value.slice(pos, end).trimEnd();
    // Group multi-character indices on the assignment's left side too.
    source = source.trimEnd().replace(/_([A-Za-z0-9]+)/g, "_{$1}");
    return {source, raw, end: pos + raw.length};
  }
  function legacyMathLabel(value, pos) {
    if (pos && /[\w/\\.$]/.test(value[pos - 1])) return null;
    const match = /^(?:(?:alpha|beta|gamma|delta|sigma|theta|lambda|mu|pi|[A-Za-z])'?_(?:\{[ijklmntuvxyz0-9]{1,3}\}|[ijklmntuvxyz0-9]{1,3})|(?:alpha|beta|gamma|delta|sigma|theta|lambda|mu|pi|\|\|)(?=\s*:))(?![A-Za-z0-9_./\\])/.exec(value.slice(pos));
    if (!match) return null;
    if (/^\s*=/.test(value.slice(pos + match[0].length))) return null; // Preserve incomplete assignments.
    const raw = match[0], source = raw === "||" ? "\\parallel" : raw
      .replace(/^[A-Za-z]+/, name => asciiMathNames[name] || name).replace(/_([A-Za-z0-9]+)/g, "_{$1}");
    return {source, raw, end: pos + raw.length};
  }
  function inline(target, value, legacy = false) {
    let plain = "";
    const flush = () => {if (plain) {target.append(root.document.createTextNode(plain)); plain = "";}};
    for (let pos = 0; pos < value.length;) {
      if (value[pos] === "\\" && value[pos + 1] === "$" && !escaped(value, pos)) {
        plain += "$"; pos += 2; continue;
      }
      let delimiter = "", kind = "";
      if (!escaped(value, pos)) {
        if (value[pos] === "`") {delimiter = "`"; kind = "code";}
        else if (value.startsWith("**", pos)) {delimiter = "**"; kind = "bold";}
        else if (value.startsWith("\\(", pos)) {delimiter = "\\)"; kind = "math";}
        else if (value[pos] === "$" && value[pos + 1] !== "$" && value[pos - 1] !== "$") {delimiter = "$"; kind = "math";}
      }
      const openLength = value.startsWith("\\(", pos) ? 2 : delimiter.length;
      const end = delimiter ? endOf(value, delimiter, pos + openLength) : -1;
      const source = end > pos + openLength ? value.slice(pos + openLength, end) : "";
      // Spaces around a dollar pair usually mean two currency amounts.
      const valid = source && !source.includes("\n") && (delimiter !== "$" || source === source.trim());
      if (valid) {
        flush();
        if (kind === "code") target.append(node("code", source));
        else if (kind === "bold") {const strong = node("strong"); inline(strong, source, legacy); target.append(strong);}
        else target.append(math(source, value.slice(pos, end + delimiter.length), false));
        pos = end + delimiter.length;
      } else {
        const equation = legacy ? legacyEquation(value, pos) || legacyMathLabel(value, pos) : null;
        if (equation) {flush(); target.append(math(equation.source, equation.raw, false)); pos = equation.end;}
        else {plain += value[pos]; pos++;}
      }
    }
    flush();
  }
  function blocks(text) {
    const pieces = [];
    let start = 0, pos = 0;
    while (pos < text.length) {
      if (text.startsWith("```", pos) && /^[ \t]*$/.test(text.slice(text.lastIndexOf("\n", pos - 1) + 1, pos))) {
        const lineEnd = text.indexOf("\n", pos);
        if (lineEnd === -1) break;
        const close = /^[ \t]*```[^\n]*(?:\n|$)/gm;
        close.lastIndex = lineEnd + 1;
        const match = close.exec(text);
        if (!match) break;
        pos = close.lastIndex; continue;
      }
      if (text[pos] === "`" && !escaped(text, pos)) {
        const close = endOf(text, "`", pos + 1), lineEnd = text.indexOf("\n", pos);
        if (close !== -1 && (lineEnd === -1 || close < lineEnd)) {pos = close + 1; continue;}
      }
      const delimiter = !escaped(text, pos) && (text.startsWith("$$", pos) ? "$$" : text.startsWith("\\[", pos) ? "\\]" : "");
      if (delimiter) {
        const end = endOf(text, delimiter, pos + 2);
        if (end !== -1) {
          if (pos > start) pieces.push({text: text.slice(start, pos)});
          pieces.push({source: text.slice(pos + 2, end).trim(), raw: text.slice(pos, end + 2)});
          start = pos = end + 2; continue;
        }
      }
      pos++;
    }
    if (start < text.length) pieces.push({text: text.slice(start)});
    return pieces;
  }
  function render(text) {
    const body = node("div", undefined, "ai-home-message-text ai-home-markdown");
    for (const piece of blocks(repairDelimited(String(text || "")))) {
      if (piece.raw !== undefined) {body.append(math(piece.source, piece.raw, true)); continue;}
      let list = null, code = null;
      const lines = piece.text.split("\n");
      for (let lineIndex = 0; lineIndex < lines.length; lineIndex++) {
        const line = lines[lineIndex];
        if (line.trimStart().startsWith("```")) {
          list = null;
          if (code) code = null;
          else {const pre = node("pre"); code = node("code"); pre.append(code); body.append(pre);}
          continue;
        }
        if (code) {code.append(root.document.createTextNode(line + "\n")); continue;}
        const cells = value => value.trim().replace(/^\||\|$/g, "").split(/(?<!\\)\|/).map(cell=>cell.trim().replace(/\\\|/g,"|"));
        let separator = lineIndex + 1;
        while (separator < lines.length && !lines[separator].trim()) separator++;
        const headers = line.trim().startsWith("|") ? cells(line) : [];
        const rules = separator < lines.length ? cells(lines[separator]) : [];
        if (headers.length >= 2 && headers.length <= 16 && rules.length === headers.length && rules.every(rule=>/^:?-{3,}:?$/.test(rule))) {
          list = null;
          const wrapper = node("div", undefined, "ai-home-table-wrap"), table = node("table"), head = node("thead"), row = node("tr"), tableBody = node("tbody");
          headers.forEach(value=>{const cell=node("th"); inline(cell,value); row.append(cell);});
          head.append(row); table.append(head,tableBody); wrapper.append(table); body.append(wrapper);
          let cursor = separator + 1, count = 0;
          lineIndex = separator;
          while (cursor < lines.length && count < 200) {
            if (!lines[cursor].trim()) {cursor++; continue;}
            const values = lines[cursor].trim().startsWith("|") ? cells(lines[cursor]) : [];
            if (values.length !== headers.length) break;
            const dataRow=node("tr"); values.forEach(value=>{const cell=node("td"); inline(cell,value); dataRow.append(cell);});
            tableBody.append(dataRow); count++; lineIndex=cursor++;
          }
          continue;
        }
        const heading = line.match(/^(#{1,4})\s+(.+)$/), bullet = line.match(/^\s*([-*]|\d+[.)])\s+(.+)$/);
        if (bullet) {
          const tag = /^[\d]/.test(bullet[1]) ? "ol" : "ul";
          if (!list || list.tagName.toLowerCase() !== tag) {list = node(tag); body.append(list);}
          const item = node("li"); inline(item, bullet[2]); list.append(item); continue;
        }
        list = null;
        if (!line.trim()) continue;
        const part = node(heading ? "h" + Math.min(4, heading[1].length + 1) : "p");
        inline(part, heading ? heading[2] : line); body.append(part);
      }
    }
    return body;
  }
  // Artifact titles/cells already have their own structure. Render their text
  // and formulas without introducing paragraph or heading wrappers.
  function renderText(target, value) {
    target.textContent = "";
    for (const piece of blocks(repairDelimited(String(value ?? "")))) {
      if (piece.raw !== undefined) target.append(math(piece.source, piece.raw, true));
      else inline(target, piece.text, true);
    }
    return target;
  }
  const api = Object.freeze({render, renderText});
  root.NotionAiAnswer = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window === "undefined" ? globalThis : window);

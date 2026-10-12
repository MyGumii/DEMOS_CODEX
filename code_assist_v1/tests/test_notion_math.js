"use strict";
const assert = require("node:assert/strict");
globalThis.katex = require("../notion_static/vendor/katex-0.18.7/katex.min.js");
require("../notion_static/notion_math.js");
const math = globalThis.NotionMath;
for (const source of [String.raw`\alpha`, String.raw`$\alpha$`, String.raw`$$\alpha$$`, String.raw`\(\alpha\)`, String.raw`\[\alpha\]`]) {
  assert.equal(math.normalizeSource(source), String.raw`\alpha`);
  assert.match(math.renderHtml(source), /<mi>α<\/mi>/);
}
assert.match(math.renderHtml(String.raw`\frac{a}{b}`), /<mfrac>/);
assert.match(math.renderHtml(String.raw`\begin{aligned}a&=b\\c&=d\end{aligned}`), /<mtable/);
assert.match(math.renderHtml(String.raw`\frac{`), /equation-error/);
assert.doesNotMatch(math.renderHtml(String.raw`\href{javascript:alert(1)}{x}`), /href=/);
assert.doesNotMatch(math.renderHtml(String.raw`\includegraphics{https://example.org/x}`), /<img/);
assert.match(math.renderHtml(String.raw`\def\x{\x}\x`), /equation-error/);
assert.match(math.renderHtml(String.raw`\gdef\testmacro{a}\testmacro`), /<mi>a<\/mi>/);
assert.match(math.renderHtml(String.raw`\testmacro`), /equation-error/, "macros must not leak between equations");
assert.match(math.renderHtml(""), /equation-placeholder/);
console.log("notion_math: ok");

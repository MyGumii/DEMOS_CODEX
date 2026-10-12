"use strict";
const assert = require("node:assert/strict"), {test} = require("node:test");
globalThis.katex = require("../notion_static/vendor/katex-0.18.7/katex.min.js");
class Element {
  constructor(tag) {this.tagName = tag.toUpperCase(); this.children = []; this.dataset = {}; this.className = ""; this._text = ""; this.innerHTML = "";}
  append(...children) {this.children.push(...children);}
  set textContent(text) {this._text = text; this.children = []; this.innerHTML = "";}
  get textContent() {return this._text + this.children.map(c => c.textContent).join("");}
}
globalThis.document = {createElement: tag => new Element(tag), createTextNode: text => ({textContent: text})};
const {render, renderText} = require("../notion_static/notion_ai_answer.js");
const all = node => [node, ...(node.children || []).flatMap(all)];
const formulas = node => all(node).filter(n => n.dataset?.mathState);
const html = node => formulas(node).map(n => n.innerHTML).join("\n");
const gat = String.raw`GAT 한 층의 연산은 다음과 같은 단계를 거칩니다.

선형 변환: 모든 노드에 동일한 선형 변환 $W$를 적용합니다.
    $$z_i = W h_i$$

Attention coefficient 계산: 노드 $i$와 이웃 $j$ 사이의 중요도 점수 $e_{ij}$를 계산합니다.
    $$e_{ij} = \text{LeakyReLU}(a^T [W h_i \parallel W h_j])$$

$\parallel$은 연결(concatenation)을 의미합니다.
$a$는 학습 가능한 가중 벡터입니다.
Softmax 정규화: 중요도 점수를 확률 분포로 변환하여 attention coefficient $\alpha_{ij}$를 구합니다.
    $$\alpha_{ij} = \frac{\exp(e_{ij})}{\sum_{k \in N_i} \exp(e_{ik})}$$

$N_i$는 노드 $i$의 이웃 집합입니다 (보통 자기 자신 포함).
새로운 표현 생성: attention coefficient로 가중합하여 새로운 노드 표현 $h'_i$를 만듭니다.
    $$h'_i = \sigma\left(\sum_{j \in N_i} \alpha_{ij} W h_j\right)$$

$\sigma$는 활성화 함수(예: ELU)입니다.`;

test("reported GAT answer renders its four display formulas, subscripts, fraction and Greek symbols", () => {
  const body = render(gat), nodes = formulas(body);
  assert.equal(nodes.filter(n => n.className === "ai-home-math-block").length, 4);
  assert.equal(nodes.length, 15);
  assert.ok(nodes.every(n => n.dataset.mathState === "rendered"));
  assert.match(html(body), /<mfrac>/); assert.match(html(body), /<msub>/);
  for (const symbol of ["α", "σ", "∑", "∥"]) assert.ok(html(body).includes(symbol), symbol);
  assert.ok(body.textContent.includes("선형 변환") && body.textContent.includes("활성화 함수"));
});

test("multiline display and parentheses delimiters render using the existing offline KaTeX", () => {
  const body = render(String.raw`문장 \(\alpha_{ij}\) 입니다.
\[
\begin{aligned}
z_i &= W h_i \\
h'_i &= \sigma(z_i)
\end{aligned}
\]
후속 문장`);
  assert.equal(formulas(body).length, 2);
  assert.ok(formulas(body).every(n => n.dataset.mathState === "rendered"));
  assert.match(html(body), /<mtable/);
  assert.ok(body.textContent.includes("후속 문장"));
});

test("code fences and inline code preserve literal LaTeX; math in bold/list/heading still renders", () => {
  const body = render("## 제목 $W$\n- **가중치 $a$**\n`$\\alpha$`\n```latex\n$$\\frac{a}{b}$$\n```\n마지막 $\\sigma$");
  assert.equal(formulas(body).length, 3);
  const codes = all(body).filter(n => n.tagName === "CODE");
  assert.deepEqual(codes.map(n => n.textContent), [String.raw`$\alpha$`, String.raw`$$\frac{a}{b}$$` + "\n"]);
  assert.ok(all(body).some(n => n.tagName === "H3") && all(body).some(n => n.tagName === "STRONG"));
});

test("escaped dollars and ordinary currency do not turn into spurious formulas", () => {
  const body = render(String.raw`가격 $20.00, 예산 $50.00. 이스케이프 \$100. 수식 $x$`);
  assert.equal(formulas(body).length, 1);
  assert.ok(body.textContent.includes("가격 $20.00, 예산 $50.00. 이스케이프 $100."));
});

test("every streaming prefix is safe, keeps incomplete source visible, and eventually renders the complete formula", () => {
  const input = String.raw`분수는 $$\alpha = \frac{a}{b}$$ 입니다.`;
  for (let end = 0; end <= input.length; end++) assert.doesNotThrow(() => render(input.slice(0, end)));
  const partial = render(input.slice(0, input.indexOf("}$$") + 1));
  assert.equal(formulas(partial).length, 0);
  assert.ok(partial.textContent.includes(String.raw`$$\alpha = \frac{a}{b`));
  assert.equal(formulas(render(input))[0].dataset.mathState, "rendered");
});

test("unsupported and malformed math preserve readable source instead of deleting the answer", () => {
  const input = String.raw`앞 문장 $\unknownAiCommand{x}$ 뒤 문장 $$\frac{a}$$`;
  const body = render(input);
  assert.ok(formulas(body).every(n => n.dataset.mathState === "source"));
  assert.ok(body.textContent.includes(String.raw`$\unknownAiCommand{x}$`));
  assert.ok(body.textContent.includes("뒤 문장"));
});

test("untrusted answer HTML, TeX links/images and macro commands cannot become executable markup", () => {
  const body = render(String.raw`<script>alert(1)</script><img src=x onerror=alert(1)>
$\href{javascript:alert(1)}{x}$
$\includegraphics{https://example.test/private}$
$\htmlClass{injected}{x}$`);
  assert.ok(body.textContent.includes("<script>alert(1)</script>"));
  assert.doesNotMatch(html(body), /<script|<img|<a\b|href=|class="injected"|onerror=/);
  assert.ok(formulas(body).length === 3);
  assert.match(html(render(String.raw`$\gdef\aiTest{a}\aiTest$`)), /<mi>a<\/mi>/);
  assert.equal(formulas(render(String.raw`$\aiTest$`))[0].dataset.mathState, "source");
});

test("missing renderer keeps text intact rather than throwing during history load", () => {
  const existing = globalThis.katex;
  try {
    globalThis.katex = null;
    const body = render(String.raw`한글과 😀, 수식 $\alpha$`);
    assert.ok(body.textContent.includes(String.raw`한글과 😀, 수식 $\alpha$`));
  } finally {globalThis.katex = existing;}
});

test("completed formulas are reused when subsequent streaming text is painted", () => {
  const existing = globalThis.katex;
  let renders = 0;
  try {
    globalThis.katex = {renderToString(...args) {renders++; return existing.renderToString(...args);}};
    const source = String.raw`$\frac{98765}{43210}$ 이후`;
    render(source); render(source + " 문장"); render(source + " 문장이 늘어남");
    assert.equal(renders, 1);
  } finally {globalThis.katex = existing;}
});

test("artifact text retains its list/cell structure and safely renders inline and display math", () => {
  const item = new Element("li");
  item.textContent = "old";
  assert.equal(renderText(item, String.raw`선형 변환 $z_i = W h_i$, $$\frac{a}{b}$$ <img src=x onerror=alert(1)>`), item);
  assert.equal(item.tagName, "LI");
  assert.equal(formulas(item).length, 2);
  assert.ok(formulas(item).every(n => n.dataset.mathState === "rendered"));
  assert.match(html(item), /<mfrac>/);
  assert.ok(item.textContent.includes("<img src=x onerror=alert(1)>") && !item.textContent.includes("old"));
  assert.ok(all(item).every(n => !["P", "H2", "IMG", "SCRIPT"].includes(n.tagName)));
  const cell = renderText(new Element("td"), 0);
  assert.equal(cell.textContent, "0");
  renderText(cell, null); assert.equal(cell.textContent, "");
});

test("the real artifact preview renders formulas in slides and table cells", () => {
  const fs = require("node:fs"), vm = require("node:vm");
  const source = fs.readFileSync(require.resolve("../notion_static/notion_ai_home.js"), "utf8");
  const start = source.indexOf("  function artifactPreview("), end = source.indexOf("  function render()", start);
  const context = {window: {NotionAiAnswer: {renderText}}, node: (tag, text, className) => {
    const el = new Element(tag); if (text !== undefined) el.textContent = text; el.className = className || ""; return el;
  }};
  vm.runInNewContext(source.slice(start, end) + "this.preview = artifactPreview;", context);
  const slides = context.preview({kind:"slides", title:"GAT", slides:[{title:String.raw`가중치 $W$`, bullets:[String.raw`변환 $z_i = W h_i$`]}]});
  assert.equal(formulas(slides).length, 2);
  const table = context.preview({kind:"spreadsheet", title:"GAT", columns:[String.raw`특징 $h_i$`], rows:[[String.raw`$\alpha_{ij}$`],[0],[null]]});
  assert.equal(formulas(table).length, 2);
  assert.ok(formulas(table).every(n => n.dataset.mathState === "rendered"));
  assert.ok(all(table).some(n => n.tagName === "TD" && n.textContent === "0"));
});

test("the reported saved GAT table cell renders unmarked ASCII equations without changing their meaning", () => {
  const text = "z_i = W h_i, e_ij = LeakyReLU(a^T [W h_i || W h_j]), alpha_ij = exp(e_ij) / sum(exp(e_ik)), h'_i = sigma(sum(alpha_ij W h_j))";
  const cell = renderText(new Element("td"), text);
  assert.equal(formulas(cell).length, 4);
  assert.ok(formulas(cell).every(n => n.dataset.mathState === "rendered"));
  for (const symbol of ["α", "σ", "∑", "∥"]) assert.ok(html(cell).includes(symbol), symbol);
  assert.match(html(cell), /<msub>/); assert.match(html(cell), /<msup>/);
  assert.match(html(cell), /<mi>i<\/mi><mi>j<\/mi>/);
  assert.ok(html(cell).includes("LeakyReLU"));
  assert.equal(cell.textContent, ", , , ");
  // The full answer keeps its existing Markdown behavior; this fallback is for artifact text.
  assert.equal(formulas(render(text)).length, 0);
});

test("artifact equation compatibility preserves prose, code, numbers and malformed equations", () => {
  const literal = "user_name = admin, report_v2.csv, https://example.test/z_i = W, 가격 $20.00, `z_i = W h_i`, z_i = exp(";
  const cell = renderText(new Element("td"), literal);
  assert.equal(formulas(cell).length, 0);
  assert.ok(cell.textContent.includes("user_name = admin"));
  assert.ok(cell.textContent.includes("z_i = exp("));
  const safe = renderText(new Element("td"), "변환 z_i = W h_i입니다. <img onerror=alert(1)> 다음 설명");
  assert.equal(formulas(safe).length, 1);
  assert.ok(safe.textContent.includes("입니다. <img onerror=alert(1)> 다음 설명"));
  assert.doesNotMatch(html(safe), /<img|onerror=/);
});

test("GAT description and variable columns render mathematical labels while filenames and code remain literal", () => {
  const cell = renderText(new Element("td"), "가중치 alpha_ij를 얻습니다. h_i: 입력 특징, N_i: 이웃 집합, h'_i: 새 특징, sigma: 활성화 함수, ||: 연결");
  assert.equal(formulas(cell).length, 6);
  assert.ok(formulas(cell).every(n => n.dataset.mathState === "rendered"));
  for (const symbol of ["α", "σ", "∥"]) assert.ok(html(cell).includes(symbol));
  assert.ok(cell.textContent.includes("가중치") && cell.textContent.includes("활성화 함수"));
  const literal = "user_name = admin, report_v2.csv, h_i.csv, C:/h_i/file, alpha release, `h_i`, 가격 $20.00";
  const preserved = renderText(new Element("td"), literal);
  assert.equal(formulas(preserved).length, 0);
  assert.equal(preserved.textContent, literal.replaceAll("`", ""));
});

test("JSON control escapes inside TeX render in streaming and saved research without changing code", () => {
  const text = '가중치 $\beta_{ij}$와 $\frac{x}{y}$, $\tau$, $\nabla x$';
  const body = render(text);
  assert.equal(formulas(body).length, 4);
  assert.ok(formulas(body).every(n => n.dataset.mathState === "rendered"));
  assert.ok(html(body).includes("β") && html(body).includes("∇"));
  assert.equal(formulas(render('`$\beta$`')).length, 0);
});

test("research database tables render rows, escaped content and math with spaced Markdown", () => {
  const body=render('| 순위 | 작업 |\n\n| :--- | ---: |\n\n| 12 | GAT $\\alpha_{ij}$ |\n\n| 2 | <img onerror=alert(1)> |\n\n다음 문장');
  const rows=all(body).filter(n=>n.tagName === 'TR');
  assert.equal(rows.length,3);
  assert.equal(formulas(body).length,1);
  assert.ok(body.textContent.includes('다음 문장') && body.textContent.includes('<img onerror=alert(1)>'));
  assert.doesNotMatch(html(body), /<img|onerror=/);
  const code=render('```\n| 순위 | 작업 |\n| --- | --- |\n| 12 | GAT |\n```');
  assert.equal(all(code).filter(n=>n.tagName === 'TABLE').length,0);
});

test("deep report contents include rendered chapter headings and jump to the selected chapter", () => {
  const fs = require("node:fs"), vm = require("node:vm"), path = require("node:path");
  const source = fs.readFileSync(path.join(__dirname, "../notion_static/notion_ai_home.js"), "utf8");
  const body = render("# 보고서\n## 1. 요약과 결론\n### 세부 항목\n## 2. 실험 조건\n본문");
  body.querySelectorAll = selector => all(body).filter(n => selector.toUpperCase().split(/,\s*/).includes(n.tagName));
  let selected;
  for (const heading of all(body)) heading.scrollIntoView = options => {selected = {title:heading.textContent, options};};
  const context = {node: (tag, text) => {
    const element = new Element(tag); element.textContent = text || "";
    element.setAttribute = (key, value) => {element[key] = value;};
    element.addEventListener = (event, callback) => {element[event] = callback;};
    return element;
  }};
  vm.createContext(context);
  vm.runInContext(source.slice(source.indexOf("  function reportContents("), source.indexOf("  function render()")), context);
  const toc = context.reportContents(body);
  assert.equal(toc["aria-label"], "보고서 목차");
  assert.deepEqual(toc.children.map(n => n.textContent), ["보고서", "1. 요약과 결론", "2. 실험 조건"]);
  toc.children[2].click();
  assert.equal(selected.title, "2. 실험 조건"); assert.equal(selected.options.block, "start");
});

test("report comparison formulas and a two-lane schematic render offline as a table and figure", () => {
  const body=render(String.raw`| 방법 | 수식 |
| --- | --- |
| GAT | $e(h_i,h_j)=\operatorname{LeakyReLU}(a^{\top}[Wh_i\parallel Wh_j])$ |
| GATv2 | $e(h_i,h_j)=a^{\top}\operatorname{LeakyReLU}(W[h_i\parallel h_j])$ |

$$
\begin{array}{cc}
\textbf{GAT} & \textbf{GATv2} \\
\boxed{\text{입력 hᵢ, hⱼ}} & \boxed{\text{입력 hᵢ, hⱼ}} \\
\downarrow & \downarrow \\
\boxed{\text{내적 aᵀ}} & \boxed{\text{LeakyReLU 적용}} \\
\downarrow & \downarrow \\
\boxed{\text{LeakyReLU 적용}} & \boxed{\text{내적 aᵀ}}
\end{array}
$$`);
  assert.equal(all(body).filter(n=>n.tagName==='TABLE').length,1);
  assert.equal(formulas(body).length,3);
  assert.ok(formulas(body).every(n=>n.dataset.mathState==='rendered'));
  assert.match(html(body),/<mtable/); assert.ok(html(body).includes('↓'));
});

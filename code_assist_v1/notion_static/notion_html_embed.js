/* Static HTML preview in an opaque, scriptless, network-free sandbox. */
(function (root) {
  "use strict";
  const MAX_SOURCE = 200000;
  const escape = value => String(value || "").replace(/[&<>"']/g, c => ({"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"}[c]));
  function documentSource(source) {
    const template = document.createElement("template");
    template.innerHTML = String(source || "").slice(0, MAX_SOURCE);
    template.content.querySelectorAll("script,iframe,object,embed,base,meta,link,form,template,svg,math").forEach(node => node.remove());
    template.content.querySelectorAll("*").forEach(node => {
      for (const attr of [...node.attributes]) {
        if (/^on/i.test(attr.name) || /^(href|src|srcset|action|formaction|target|srcdoc|ping|background|poster|data|autofocus)$/i.test(attr.name)) {
          if (node.tagName === "IMG" && attr.name === "src" && /^data:image\/(png|jpeg|gif|webp);base64,/i.test(attr.value)) continue;
          node.removeAttribute(attr.name);
        }
      }
    });
    return `<!doctype html><html><head><meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'none'; style-src 'unsafe-inline'; img-src data:; form-action 'none'; base-uri 'none'"><meta charset="utf-8"><style>body{font:14px/1.5 sans-serif;margin:16px;overflow-wrap:anywhere}img{max-width:100%}</style></head><body>${template.innerHTML}</body></html>`;
  }
  function html(block, editable) {
    const source = String(block.content || "");
    return `<div class="html-embed-block">
      <div class="html-embed-toolbar"><span>HTML · 임베드</span>${editable ? '<button type="button" data-html-edit>HTML 편집</button>' : ""}</div>
      <div data-html-preview>${source ? `<iframe title="HTML 미리보기" sandbox="" referrerpolicy="no-referrer" srcdoc="${escape(documentSource(source))}"></iframe>` : '<div class="html-embed-empty">HTML을 입력하거나 파일을 선택하세요.</div>'}</div>
      ${editable ? `<div data-html-editor ${source ? "hidden" : ""}><textarea aria-label="HTML 소스" maxlength="${MAX_SOURCE}" spellcheck="false">${escape(source)}</textarea><div class="html-embed-actions"><label>HTML 파일 선택 <input type="file" accept=".html,.htm,text/html" data-html-file></label><button type="button" data-html-apply>완료</button></div><small>정적 HTML/CSS 미리보기입니다. 스크립트·외부 통신·폼 제출은 실행하지 않습니다.</small><div role="status" data-html-status></div></div>` : ""}
    </div>`;
  }
  function bind(row, adapter) {
    const editor = row.querySelector("[data-html-editor]");
    if (!editor) return;
    const input = editor.querySelector("textarea");
    const status = editor.querySelector("[data-html-status]");
    const valid = () => row.isConnected && adapter.canEdit() && adapter.getBlock()?.props?.embed_mode === "html";
    row.querySelector("[data-html-edit]").addEventListener("click", () => {
      if (!valid()) return;
      editor.hidden = false; input.focus();
    });
    editor.querySelector("[data-html-file]").addEventListener("change", async event => {
      const file = event.target.files?.[0];
      if (!file || !valid()) return;
      if (file.size > MAX_SOURCE) { status.textContent = "HTML 파일은 200 KB 이하여야 합니다."; return; }
      try {
        const text = await file.text();
        if (valid()) { input.value = text.slice(0, MAX_SOURCE); status.textContent = "파일을 읽었습니다. 완료를 누르면 저장합니다."; }
      } catch (_) { status.textContent = "파일을 읽지 못했습니다."; }
    });
    editor.querySelector("[data-html-apply]").addEventListener("click", () => {
      if (!valid()) { editor.hidden = true; return; }
      const source = input.value.slice(0, MAX_SOURCE);
      adapter.change(source);
      row.querySelector("[data-html-preview]").innerHTML = `<iframe title="HTML 미리보기" sandbox="" referrerpolicy="no-referrer" srcdoc="${escape(documentSource(source))}"></iframe>`;
      editor.hidden = true; status.textContent = "";
      Promise.resolve(adapter.save()).catch(() => {});
    });
  }
  root.NotionHtmlEmbed = Object.freeze({ html, bind });
})(typeof window === "undefined" ? globalThis : window);

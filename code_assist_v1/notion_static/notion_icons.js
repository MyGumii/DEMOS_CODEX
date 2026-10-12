(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  root.NotionIcons = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";
  let catalog = {vectors: [], emojis: [], colors: []}, loading;
  let pickerSequence = 0;
  const fallbackPath = "M13 3H6a1 1 0 0 0-1 1v16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V9l-6-6Z M13 3v6h6 M8 13h8 M8 17h6";
  const escape = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const recentLimit = 12;
  const recentCategory = "__recent__";
  const skinModifiers = ["", "🏻", "🏼", "🏽", "🏾", "🏿"];
  const skinNames = ["기본", "밝은 피부", "약간 밝은 피부", "중간 피부", "약간 어두운 피부", "어두운 피부"];
  function recentKey(scope) { return `notion.iconRecent.v1.${encodeURIComponent(String(scope))}`; }
  function skinKey(scope) { return `notion.iconSkinTone.v1.${encodeURIComponent(String(scope))}`; }
  function readSkinTone(scope, storage) {
    if (!scope) return 0;
    try {
      const value = Number((storage || globalThis.localStorage).getItem(skinKey(scope)));
      return Number.isInteger(value) && value >= 0 && value < skinModifiers.length ? value : 0;
    } catch { return 0; }
  }
  function saveSkinTone(scope, tone, storage) {
    if (!scope || !Number.isInteger(tone) || tone < 0 || tone >= skinModifiers.length) return;
    try { (storage || globalThis.localStorage).setItem(skinKey(scope), String(tone)); }
    catch { /* A storage failure does not block icon selection. */ }
  }
  function tonedValue(item, tone) {
    if (!item?.skinTone || !tone) return item?.value || "";
    return catalog.emojis.find(entry => entry.toneBase === item.value && entry.tone === tone)?.value || item.value;
  }
  function searchItems(items, query, kind = "emoji") {
    const matches = items.filter(item => `${item.label} ${item.terms} ${item.value || ""}`.toLocaleLowerCase().includes(query));
    const observed = kind === "emoji" && query ? catalog.observedSearch?.[query] : null;
    if (!Array.isArray(observed)) return matches;
    const byValue = new Map(items.map(item => [item.value, item]));
    const ranked = observed.map(value => byValue.get(value)).filter(Boolean);
    const rankedValues = new Set(ranked.map(item => item.value));
    return [...ranked, ...matches.filter(item => !rankedValues.has(item.value))];
  }
  function validRecent(value) {
    const parsed = parse(value);
    return parsed.kind === "vector" || (parsed.kind === "emoji" && catalog.emojis.some(item => item.value === parsed.value));
  }
  function readRecent(scope, storage) {
    if (!scope) return [];
    try {
      const saved = JSON.parse((storage || globalThis.localStorage).getItem(recentKey(scope)) || "[]");
      return Array.isArray(saved) ? [...new Set(saved.filter(value => typeof value === "string" && validRecent(value)))].slice(0, recentLimit) : [];
    } catch { return []; }
  }
  function recordRecent(scope, value, storage) {
    if (!scope || !validRecent(value)) return;
    try {
      const target = storage || globalThis.localStorage;
      target.setItem(recentKey(scope), JSON.stringify([value, ...readRecent(scope, target).filter(item => item !== value)].slice(0, recentLimit)));
    } catch { /* Icon saves still succeed when browser storage is unavailable. */ }
  }
  function configure(value) { catalog = value; return catalog; }
  async function load(fetcher = globalThis.fetch) {
    if (!loading) loading = fetcher("/notion_static/assets/icons/catalog.json?v=20260927-ui7-house-search")
      .then(response => { if (!response.ok) throw new Error("아이콘 목록을 불러오지 못했습니다."); return response.json(); })
      .then(configure).catch(error => { loading = null; throw error; });
    return loading;
  }
  function parse(raw) {
    const value = String(raw || "").trim();
    if (!value) return {kind: "default"};
    if (value === "list") return {kind: "vector", id: "list", color: "default"};
    const vector = /^ni:([a-z][a-z0-9-]{0,32}):([a-z]+)$/.exec(value);
    if (vector && catalog.vectors.some(i => i.id === vector[1]) && catalog.colors.some(c => c.id === vector[2])) {
      return {kind: "vector", id: vector[1], color: vector[2]};
    }
    const file = /^nf:((?:f_|file_)[a-zA-Z0-9_-]{1,56})$/.exec(value);
    if (file) return {kind: "file", id: file[1]};
    const workspaceFile = /^nw:(wi_[a-f0-9]{40})$/.exec(value);
    if (workspaceFile) return {kind: "workspace-file", id: workspaceFile[1]};
    const customEmoji = /^ne:(we_[a-f0-9]{40})$/.exec(value);
    if (customEmoji) return {kind: "custom-emoji", id: customEmoji[1]};
    const personalFile = /^np:([a-z0-9][a-z0-9_.-]{1,63}):(pi_[a-f0-9]{40})$/.exec(value);
    if (personalFile) return {kind: "personal-workspace-file", owner: personalFile[1], id: personalFile[2]};
    if (/^(ni:|nf:|nw:|np:|ne:)/.test(value)) return {kind: "default"};
    return {kind: "emoji", value};
  }
  function vectorValue(id, color = "default") {
    const value = `ni:${id}:${color}`;
    if (parse(value).kind !== "vector") throw new Error("지원하지 않는 아이콘입니다.");
    return value;
  }
  function fileValue(id) {
    const value = `nf:${id}`;
    if (value.length > 64 || parse(value).kind !== "file") throw new Error("이미지 파일 정보를 확인할 수 없습니다.");
    return value;
  }
  function fileUrl(id) { return `/api/notion/${id.startsWith("file_") ? "collaboration/" : ""}files/${encodeURIComponent(id)}`; }
  function vectorSvg(id, color = "default") {
    if (id === "doc") return `<svg class="notion-vector-icon" data-icon-id="doc" data-icon-color="${escape(color)}" viewBox="4.12 2.37 11.75 15.25" fill="currentColor" aria-hidden="true"><path d="M13.3 14.25a.55.55 0 0 1-.55.55h-5.5a.55.55 0 1 1 0-1.1h5.5a.55.55 0 0 1 .55.55m-.55-1.95a.55.55 0 1 0 0-1.1h-5.5a.55.55 0 0 0 0 1.1z"/><path d="M6.25 2.375A2.125 2.125 0 0 0 4.125 4.5v11c0 1.174.951 2.125 2.125 2.125h7.5a2.125 2.125 0 0 0 2.125-2.125V8.121c0-.563-.224-1.104-.622-1.502L11.63 2.997a2.13 2.13 0 0 0-1.502-.622zM5.375 4.5c0-.483.392-.875.875-.875h3.7V6.25A2.05 2.05 0 0 0 12 8.3h2.625v7.2a.875.875 0 0 1-.875.875h-7.5a.875.875 0 0 1-.875-.875zm8.691 2.7H12a.95.95 0 0 1-.95-.95V4.184z"/></svg>`;
    const path = catalog.vectors.find(i => i.id === id)?.path || fallbackPath;
    return `<svg class="notion-vector-icon" data-icon-id="${escape(id)}" data-icon-color="${escape(color)}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${escape(path)}"/></svg>`;
  }
  function content(raw) {
    const icon = parse(raw);
    if (icon.kind === "default") return vectorSvg("doc");
    if (icon.kind === "vector") return vectorSvg(icon.id, icon.color);
    if (icon.kind === "file") return `<img class="notion-icon-image" src="${escape(fileUrl(icon.id))}" alt="" draggable="false" loading="lazy">`;
    if (icon.kind === "workspace-file") return `<img class="notion-icon-image" src="/api/notion/workspace-images/${escape(icon.id)}" alt="" draggable="false" loading="lazy">`;
    if (icon.kind === "custom-emoji") return `<img class="notion-emoji-image" src="/api/notion/custom-emoji/${escape(icon.id)}" alt="" draggable="false" loading="lazy">`;
    if (icon.kind === "personal-workspace-file") return `<img class="notion-icon-image" src="/api/notion/personal-workspaces/${encodeURIComponent(icon.owner)}/images/${escape(icon.id)}" alt="" draggable="false" loading="lazy">`;
    const item = catalog.emojis.find(i => i.value === icon.value);
    if (item) return `<img class="notion-emoji-image" src="/notion_static/assets/emoji/${escape(item.asset)}.svg" alt="" draggable="false" loading="lazy">`;
    return `<span class="notion-icon-emoji">${escape(icon.value)}</span>`;
  }
  function pickerContent(raw) { return content(raw).replace('loading="lazy"', 'loading="eager"'); }
  function html(value, extraClass = "") {
    const raw = typeof value === "string" ? value : value?.icon;
    return `<span class="page-custom-icon ${escape(extraClass)}">${content(raw)}</span>`;
  }
  function initial(value) {
    const text = String(value || "").trim() || "W";
    return globalThis.Intl?.Segmenter ? [...new Intl.Segmenter(undefined, {granularity: "grapheme"}).segment(text)][0].segment : [...text][0];
  }
  function workspace(value, fallback = "W") {
    return value?.icon ? content(value.icon) : `<span class="workspace-initial">${escape(initial(value?.name || fallback))}</span>`;
  }
  async function validateImage(file) {
    if (!file || !file.size || file.size > 2 * 1024 * 1024) throw new Error("2MB 이하의 PNG, JPEG, WebP 이미지를 선택해 주세요.");
    const bytes = new Uint8Array(await file.slice(0, 12).arrayBuffer());
    const mime = bytes[0] === 137 && bytes[1] === 80 && bytes[2] === 78 && bytes[3] === 71
      && bytes[4] === 13 && bytes[5] === 10 && bytes[6] === 26 && bytes[7] === 10 ? "image/png"
      : bytes[0] === 255 && bytes[1] === 216 && bytes[2] === 255 ? "image/jpeg"
      : String.fromCharCode(...bytes.slice(0, 4)) === "RIFF" && String.fromCharCode(...bytes.slice(8, 12)) === "WEBP" ? "image/webp" : "";
    if (!mime || (file.type && file.type !== mime)) throw new Error("PNG, JPEG, WebP 이미지 파일만 사용할 수 있습니다.");
    return mime;
  }
  function clipboardImage(data) {
    const file = Array.from(data?.files || []).find(item => item.type?.startsWith("image/"));
    if (file) return file;
    for (const item of Array.from(data?.items || [])) {
      if (item.kind === "file" && item.type?.startsWith("image/")) {
        const image = item.getAsFile();
        if (image) return image;
      }
    }
    return null;
  }
  function clipboardUrl(data) {
    const value = String(data?.getData?.("text/plain") || "").trim();
    if (!/^https?:\/\//i.test(value) || value.length > 2048 || /[\s\\]/.test(value)) return "";
    try {
      const url = new URL(value);
      return url.hostname && !url.username && !url.password ? url.href : "";
    } catch { return ""; }
  }
  function nextIconIndex(rectangles, index, key) {
    if (!rectangles.length) return -1;
    index = Math.max(0, Math.min(rectangles.length - 1, index));
    if (key === "Home") return 0;
    if (key === "End") return rectangles.length - 1;
    if (key === "ArrowLeft" || key === "ArrowRight") return Math.max(0, Math.min(rectangles.length - 1, index + (key === "ArrowLeft" ? -1 : 1)));
    if (key !== "ArrowUp" && key !== "ArrowDown") return index;
    const current = rectangles[index], direction = key === "ArrowUp" ? -1 : 1;
    const candidates = rectangles.map((rect, i) => ({i, dy: (rect.top - current.top) * direction,
      dx: Math.abs(rect.left + rect.width / 2 - current.left - current.width / 2)})).filter(item => item.dy > 1);
    if (!candidates.length) return index;
    const distance = Math.min(...candidates.map(item => item.dy));
    return candidates.filter(item => Math.abs(item.dy - distance) < 1).sort((a,b) => a.dx - b.dx || a.i - b.i)[0].i;
  }
  function categorySymbol(label) {
    const ids = {"문서와 업무":"doc", "협업과 연결":"building", "개발과 학습":"code", "상태와 목표":"flag",
      "생활과 활동":"coffee", "업무와 학습":"book", "자연과 장소":"leaf", "기호와 상태":"star"};
    const path = label === recentCategory ? "M12 3a9 9 0 1 1-8.49 6 M3 3v6h6 M12 7v5l3 2"
      : !label ? "M3 3h7v7H3Z M14 3h7v7h-7Z M3 14h7v7H3Z M14 14h7v7h-7Z"
      : label === "사람" ? "M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z M8 9h.01 M16 9h.01 M8 14c2 3 6 3 8 0"
      : catalog.vectors.find(item => item.id === ids[label])?.path || fallbackPath;
    return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${escape(path)}"/></svg>`;
  }
  function mount(root, host) {
    let tab = parse(host.value).kind === "vector" ? "vector" : "emoji";
    let color = parse(host.value).color || "default", group = "", query = "", busy = false, disposed = false;
    let selectedFile = null, preview = "", uploadedValue = "", selectionVersion = 0, remoteAbort = null;
    let activeValue = "", colorsOpen = false, skinOpen = false, categoriesTab = "", recentColorOverride = false;
    let customItems = [], customOpen = false, customFile = null, customRequestToken = 0;
    let skinTone = readSkinTone(host.recentScope);
    const resultsId = `icon-picker-results-${++pickerSequence}`;
    const skinMenuId = `${resultsId}-skin`;
    const abort = new AbortController();
    root.classList.add("page-icon-picker");
    root.innerHTML = `<div class="icon-picker-tabs" role="tablist" aria-label="아이콘 종류">
      <button type="button" role="tab" data-icon-tab="emoji">이모지</button><button type="button" role="tab" data-icon-tab="vector">아이콘</button>${host.allowUpload === false ? "" : '<button type="button" role="tab" data-icon-tab="upload">업로드</button>'}
      <button type="button" data-icon-remove>제거</button></div>
      <div data-icon-catalog><div class="icon-picker-search"><div class="icon-picker-search-field"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><circle cx="10.8" cy="10.8" r="6.5"/><path d="m16 16 4.5 4.5"/></svg><input type="search" role="combobox" aria-autocomplete="list" aria-haspopup="listbox" aria-controls="${resultsId}" aria-label="아이콘 검색" placeholder="검색"></div><button type="button" data-icon-random aria-label="랜덤 아이콘" title="랜덤"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 7h3c3 0 4.5 2 5 5s2 5 5 5h3M17 4l3 3-3 3M4 17h3c1.7 0 2.8-.7 3.6-2M14 9c.8-1.3 1.8-2 3-2h3M17 14l3 3-3 3"/></svg></button><button type="button" data-icon-skin-toggle aria-haspopup="menu" aria-controls="${skinMenuId}" aria-expanded="false" aria-label="피부색 선택"></button><button type="button" data-icon-color-toggle aria-haspopup="dialog" aria-controls="${resultsId}-colors" aria-expanded="false" hidden><span></span></button>
      <div id="${skinMenuId}" class="icon-picker-skin-menu" role="menu" aria-label="피부색 선택" hidden>${skinNames.map((name,index) => `<button type="button" role="menuitemradio" data-skin-tone="${index}" aria-label="${name}" aria-checked="false" title="${name}">${pickerContent(`✋${skinModifiers[index]}`)}</button>`).join("")}</div>
      <div id="${resultsId}-colors" class="icon-picker-color-popover" role="dialog" aria-label="아이콘 색상 선택" hidden><div class="icon-picker-colors" role="radiogroup" aria-label="아이콘 색상">${catalog.colors.map(c=>`<button type="button" role="radio" data-color="${c.id}" data-icon-color="${c.id}" aria-label="${c.label}" title="${c.label}"><span></span></button>`).join("")}</div></div></div>
      <div id="${resultsId}" class="icon-picker-results" role="listbox" aria-label="아이콘 검색 결과" tabindex="-1"></div><div class="icon-picker-categories" role="toolbar" aria-label="아이콘 분류"></div></div>
      <div class="icon-picker-upload" role="tabpanel" aria-label="아이콘 업로드" hidden><div class="icon-upload-preview" role="group" aria-label="아이콘 미리보기" hidden><div class="icon-upload-heading">미리 보기</div><div class="icon-upload-samples"></div></div><div data-icon-upload-input><label class="icon-file-label" title="PNG, JPEG, WebP · 최대 2MB"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="8" cy="9" r="1.5"/><path d="m3 17 5-5 4 4 4-6 5 7"/></svg><span>이미지 업로드</span><input type="file" accept="image/png,image/jpeg,image/webp" aria-label="아이콘 이미지 파일"></label><p>또는 Ctrl+V 단축키로 이미지${host.loadUrl ? "나 링크" : ""}를 붙여넣으세요.</p></div><div class="icon-upload-actions"><button type="button" data-icon-cancel>취소</button><button type="button" data-icon-back hidden>뒤로</button><button type="button" class="primary" data-icon-apply disabled>저장</button></div></div>
      <div class="icon-picker-custom-form" role="group" aria-label="사용자 지정 이모지 추가" hidden><h3>사용자 지정 이모지 추가</h3><p>이 워크스페이스의 누구나 사용할 수 있습니다.</p><label class="icon-file-label" title="PNG, JPEG, WebP · 최대 2MB"><span>이미지 업로드</span><input type="file" data-icon-custom-file accept="image/png,image/jpeg,image/webp" aria-label="사용자 지정 이모지 이미지"></label><label class="icon-custom-name-label">이모지 이름<input type="text" data-icon-custom-name maxlength="64" aria-label="이모지 이름"></label><div class="icon-upload-actions"><button type="button" data-icon-custom-cancel>취소</button><button type="button" class="primary" data-icon-custom-save disabled>저장</button></div></div>
      <div class="icon-picker-status" role="status" aria-live="polite"></div>
      <a class="icon-picker-credit" href="/notion_static/assets/emoji/NOTICE.txt" target="_blank" rel="noopener">Twemoji · CC BY 4.0</a>`;
    const find = s => root.querySelector(s);
    const status = (text, error = false) => { find(".icon-picker-status").textContent = text; find(".icon-picker-status").classList.toggle("error", error); };
    function setBusy(value) {
      busy = value; root.setAttribute("aria-busy", String(value));
      root.querySelectorAll("button,input").forEach(el => { el.disabled = value; });
      find("[data-icon-apply]").disabled = value || !selectedFile;
      if (host.customEmoji) {
        const customSave = find("[data-icon-custom-save]");
        if (customSave) customSave.disabled = value || !customFile || !find("[data-icon-custom-name]").value.trim();
      }
    }
    async function apply(value, file = null) {
      if (busy || disposed) return;
      selectionVersion++;
      remoteAbort?.abort();
      setBusy(true); status(file ? "이미지 업로드 중…" : "아이콘 저장 중…");
      try {
        if (file && host.saveFile) {
          await host.saveFile(file);
          if (!disposed) host.close();
          return;
        }
        if (file && !uploadedValue) {
          const id = await host.upload(file, abort.signal);
          uploadedValue = fileValue(id);
        }
        if (disposed) return;
        await host.save(file ? uploadedValue : value);
        if (!file) recordRecent(host.recentScope, value);
        if (!disposed) host.close();
      } catch (error) { if (!disposed) status(error.message || "아이콘을 저장하지 못했습니다. 다시 시도해 주세요.", true); }
      finally { if (!disposed) setBusy(false); }
    }
    function setActive(button, focus = false, scroll = false) {
      activeValue = button?.dataset.iconValue || "";
      root.querySelectorAll("[data-icon-value]").forEach(item => {
        item.setAttribute("aria-selected", String(item === button)); item.tabIndex = item === button ? 0 : -1;
      });
      const search = find('input[type="search"]');
      if (button) search.setAttribute("aria-activedescendant", button.id);
      else search.removeAttribute("aria-activedescendant");
      if (focus) button?.focus();
      if (scroll) button?.scrollIntoView({block:"nearest", inline:"nearest"});
    }
    function renderColors() {
      const toggle = find("[data-icon-color-toggle]");
      const label = catalog.colors.find(item => item.id === color)?.label || "기본";
      toggle.hidden = tab !== "vector";
      toggle.dataset.iconColor = color;
      toggle.setAttribute("aria-label", `아이콘 색상 선택, ${label} 선택됨`);
      toggle.title = `아이콘 색상 · ${label}`;
      toggle.setAttribute("aria-expanded", String(colorsOpen));
      find(".icon-picker-color-popover").hidden = !colorsOpen;
      root.querySelectorAll("[data-color]").forEach(el => {
        const selected = el.dataset.color === color;
        el.setAttribute("aria-checked", String(selected)); el.tabIndex = selected ? 0 : -1;
      });
    }
    function renderSkin() {
      const toggle = find("[data-icon-skin-toggle]");
      toggle.hidden = tab !== "emoji";
      toggle.innerHTML = pickerContent(`✋${skinModifiers[skinTone]}`);
      toggle.setAttribute("aria-label", `피부색 선택, ${skinNames[skinTone]} 선택됨`);
      toggle.setAttribute("aria-expanded", String(skinOpen));
      find(".icon-picker-skin-menu").hidden = !skinOpen;
      root.querySelectorAll("[data-skin-tone]").forEach(button => {
        const selected = Number(button.dataset.skinTone) === skinTone;
        button.setAttribute("aria-checked", String(selected)); button.tabIndex = selected ? 0 : -1;
      });
    }
    function setSkinMenu(open, focus = false) {
      skinOpen = open && tab === "emoji"; renderSkin();
      if (focus) (skinOpen ? find(`[data-skin-tone="${skinTone}"]`) : find("[data-icon-skin-toggle]")).focus();
    }
    function selectSkinTone(index) {
      if (!Number.isInteger(index) || index < 0 || index >= skinModifiers.length) return;
      skinTone = index; saveSkinTone(host.recentScope, index);
      skinOpen = false; activeValue = ""; render();
      find("[data-icon-skin-toggle]").focus();
    }
    function setColorMenu(open, focus = false) {
      colorsOpen = open && tab === "vector"; renderColors();
      if (focus) (colorsOpen ? find(`[data-color="${color}"]`) : find("[data-icon-color-toggle]")).focus();
    }
    function selectColor(value) {
      const active = parse(activeValue);
      color = value;
      recentColorOverride = true;
      if (active.kind === "vector") activeValue = vectorValue(active.id, color);
      render();
    }
    function customSaveEnabled() {
      const save = find("[data-icon-custom-save]");
      if (save) save.disabled = busy || !customFile || !find("[data-icon-custom-name]").value.trim();
    }
    function showCustomForm(open) {
      customOpen = Boolean(open && host.customEmoji);
      if (!customOpen) {
        customFile = null;
        customRequestToken++;
        find("[data-icon-custom-file]").value = "";
        find("[data-icon-custom-name]").value = "";
      }
      render(); customSaveEnabled();
      (customOpen ? find("[data-icon-custom-file]") : find('input[type="search"]')).focus();
    }
    async function selectCustomFile(file) {
      const version = ++customRequestToken;
      customFile = null; customSaveEnabled();
      if (!file) return;
      try {
        await validateImage(file);
        if (disposed || version !== customRequestToken) return;
        customFile = file; status(""); customSaveEnabled();
      } catch (error) { if (!disposed && version === customRequestToken) status(error.message, true); }
    }
    async function saveCustomEmoji() {
      if (busy || disposed || !customOpen || !customFile || !host.customEmoji) return;
      const name = find("[data-icon-custom-name]").value.trim(), file = customFile;
      if (!name) return;
      const requestToken = customRequestToken;
      setBusy(true); status("사용자 지정 이모지 저장 중…");
      let created = null;
      try { created = await host.customEmoji.create(name, file, requestToken, abort.signal); }
      catch (error) { if (!disposed) status(error.message || "이모지를 저장하지 못했습니다.", true); }
      finally { if (!disposed) setBusy(false); }
      if (!created || disposed) return;
      customItems = [{value:created.icon, label:created.name, terms:created.name,
        group:"사용자 지정"}, ...customItems.filter(item => item.value !== created.icon)];
      showCustomForm(false);
      query = ""; group = "사용자 지정"; find('input[type="search"]').value = "";
      render(); status("이모지가 추가됐습니다. 선택하면 페이지 아이콘으로 적용됩니다.");
      const choice = [...root.querySelectorAll("[data-icon-value]")].find(button => button.dataset.iconValue === created.icon);
      if (choice) setActive(choice, true, true);
    }
    function render() {
      const upload = tab === "upload";
      root.classList.toggle("searching", Boolean(query) && !upload && !customOpen);
      find(".icon-picker-credit").hidden = upload || customOpen;
      find(".icon-picker-tabs").hidden = customOpen;
      root.querySelectorAll("[data-icon-tab]").forEach(el => {const active = el.dataset.iconTab === tab; el.setAttribute("aria-selected", String(active)); el.tabIndex = active ? 0 : -1;});
      find("[data-icon-catalog]").hidden = upload || customOpen;
      find(".icon-picker-upload").hidden = !upload || customOpen;
      find(".icon-picker-custom-form").hidden = !customOpen;
      find('input[type="search"]').setAttribute("aria-expanded", String(!upload && !customOpen));
      if (customOpen) return;
      if (tab !== "vector") colorsOpen = false;
      if (tab !== "emoji") skinOpen = false;
      renderColors(); renderSkin();
      if (upload) {find('input[type="search"]').removeAttribute("aria-activedescendant"); return;}
      const items = tab === "vector" ? catalog.vectors : [...customItems, ...catalog.emojis.filter(item => !item.hidden)];
      const groups = [...new Set(items.map(i => i.group))];
      const recentValues = new Set();
      const recent = readRecent(host.recentScope).flatMap(value => {
        const parsed = parse(value);
        const item = parsed.kind === "vector" && tab === "vector" ? items.find(entry => entry.id === parsed.id)
          : parsed.kind === "emoji" && tab === "emoji" ? catalog.emojis.find(entry => entry.value === parsed.value) : null;
        const recentValue = parsed.kind === "vector" && recentColorOverride ? vectorValue(parsed.id, color) : value;
        if (!item || recentValues.has(recentValue)) return [];
        recentValues.add(recentValue);
        return [{...item, recentValue}];
      });
      const source = query ? items : group === recentCategory ? recent : items.filter(i => !group || group === i.group);
      const matches = searchItems(source, query, tab);
      const option = i => {
        const value = i.recentValue || (tab === "vector" ? vectorValue(i.id, color) : tonedValue(i, skinTone));
        return `<button type="button" role="option" data-icon-value="${escape(value)}" aria-label="${escape(i.label)}" title="${escape(i.terms)}">${pickerContent(value)}</button>`;
      };
      const section = (label, members) => members.length ? `<section role="group" aria-label="${escape(label)}"><div class="icon-picker-group-label">${escape(label)}</div><div class="icon-picker-grid">${members.map(option).join("")}</div></section>` : "";
      find(".icon-picker-results").innerHTML = matches.length ? (
        (query ? `<div class="icon-picker-grid icon-picker-search-grid">${matches.map(option).join("")}</div>` : "")
        + (!query && group === recentCategory ? section("최근 사용한 항목", matches) : "")
        + (!group && !query ? section("최근 사용한 항목", recent) : "")
        + (!query && group !== recentCategory ? groups.map(label => section(label, matches.filter(i => i.group === label))).join("") : "")
      ) : query ? `<p class="icon-picker-search-empty">결과 없음</p>${tab === "emoji" && host.customEmoji ? '<button type="button" class="icon-picker-custom-add" data-icon-custom-add>이모지 추가</button>' : ""}` : '<p class="icon-picker-empty">일치하는 아이콘이 없습니다.</p>';
      find(".icon-picker-categories").hidden = Boolean(query);
      if (categoriesTab !== tab) {
        find(".icon-picker-categories").innerHTML = [...(recent.length ? [recentCategory] : []),"",...groups].map(label => {
          const name = label === recentCategory ? "최근 사용한 항목" : label || "전체";
          return `<button type="button" data-icon-category="${escape(label)}" aria-label="${escape(name)}" title="${escape(name)}">${categorySymbol(label)}</button>`;
        }).join("");
        categoriesTab = tab;
      }
      root.querySelectorAll("[data-icon-category]").forEach(button => {
        const selected = button.dataset.iconCategory === group;
        button.setAttribute("aria-pressed", String(selected)); button.tabIndex = selected ? 0 : -1;
      });
      const buttons = [...root.querySelectorAll("[data-icon-value]")];
      buttons.forEach((button,index) => {button.id = `${resultsId}-${index}`;});
      setActive(buttons.find(button => button.dataset.iconValue === activeValue) || buttons[0]);
    }
    root.addEventListener("click", event => {
      const button = event.target.closest("button"); if (!button || busy || disposed) return;
      if (button.hasAttribute("data-icon-skin-toggle")) {setSkinMenu(!skinOpen, true); return;}
      if (button.hasAttribute("data-skin-tone")) {selectSkinTone(Number(button.dataset.skinTone)); return;}
      if (skinOpen) setSkinMenu(false);
      if (button.hasAttribute("data-icon-color-toggle")) {setColorMenu(!colorsOpen, true); return;}
      if (colorsOpen && !button.hasAttribute("data-color")) setColorMenu(false);
      if (button.hasAttribute("data-icon-tab")) {tab = button.dataset.iconTab; group = ""; render();}
      else if (button.hasAttribute("data-icon-category")) {group = button.dataset.iconCategory; render(); find(".icon-picker-results").scrollTop = 0;}
      else if (button.hasAttribute("data-color")) {selectColor(button.dataset.color); setColorMenu(false, true);}
      else if (button.hasAttribute("data-icon-value")) apply(button.dataset.iconValue);
      else if (button.hasAttribute("data-icon-custom-add")) showCustomForm(true);
      else if (button.hasAttribute("data-icon-custom-cancel")) showCustomForm(false);
      else if (button.hasAttribute("data-icon-custom-save")) void saveCustomEmoji();
      else if (button.hasAttribute("data-icon-remove")) apply("");
      else if (button.hasAttribute("data-icon-cancel")) {selectionVersion++; remoteAbort?.abort(); host.close();}
      else if (button.hasAttribute("data-icon-back")) {resetImageSelection(true); status("");}
      else if (button.hasAttribute("data-icon-apply")) apply("", selectedFile);
      else if (button.hasAttribute("data-icon-random")) {
        const items = [...root.querySelectorAll("[data-icon-value]")];
        if (items.length) apply(items[Math.floor(Math.random() * items.length)].dataset.iconValue);
      }
    });
    find('input[type="search"]').addEventListener("input", event => {
      query = event.target.value.trim().toLocaleLowerCase(); group = ""; activeValue = "";
      find(".icon-picker-results").scrollTop = 0; render();
    });
    root.addEventListener("focusin", event => {if (event.target.hasAttribute?.("data-icon-value")) setActive(event.target);});
    root.addEventListener("focusout", event => {
      if (skinOpen && event.relatedTarget && !find(".icon-picker-skin-menu").contains(event.relatedTarget)
        && event.relatedTarget !== find("[data-icon-skin-toggle]")) setSkinMenu(false);
      if (colorsOpen && event.relatedTarget && !find(".icon-picker-color-popover").contains(event.relatedTarget)
        && event.relatedTarget !== find("[data-icon-color-toggle]")) setColorMenu(false);
    });
    function resetImageSelection(focus = false) {
      selectionVersion++; selectedFile = null; uploadedValue = "";
      remoteAbort?.abort(); remoteAbort = null;
      if (preview) URL.revokeObjectURL(preview); preview = "";
      find(".icon-upload-samples").replaceChildren(); find(".icon-upload-preview").hidden = true;
      find("[data-icon-upload-input]").hidden = false; find("[data-icon-back]").hidden = true;
      find("[data-icon-cancel]").hidden = false; find("[data-icon-apply]").disabled = true;
      find('input[type="file"]').value = "";
      if (focus && tab === "upload") find('input[type="file"]').focus();
    }
    async function selectImage(file, remoteUrl = "") {
      if ((!file && !remoteUrl) || busy || disposed) return;
      const active = root.ownerDocument?.activeElement;
      const restoreFocus = [find('input[type="file"]'), find("[data-icon-back]"), find("[data-icon-apply]")].includes(active);
      resetImageSelection(restoreFocus);
      const version = selectionVersion;
      remoteAbort = remoteUrl ? new AbortController() : null;
      status("이미지 확인 중…");
      try {
        if (remoteUrl) file = await host.loadUrl(remoteUrl, remoteAbort.signal);
        if (disposed || version !== selectionVersion) return;
        await validateImage(file);
        if (disposed || version !== selectionVersion) return;
        const url = URL.createObjectURL(file), img = new Image(); img.alt = "밝은 배경 아이콘 미리보기";
        try { img.src = url; await img.decode(); } catch {URL.revokeObjectURL(url); throw new Error("이미지를 열 수 없습니다. 다른 파일을 선택해 주세요.");}
        if (disposed || version !== selectionVersion) {URL.revokeObjectURL(url); return;}
        const dark = new Image(); dark.src = url; dark.alt = "어두운 배경 아이콘 미리보기";
        img.draggable = dark.draggable = false;
        const focusFromInput = tab === "upload" && root.ownerDocument?.activeElement === find('input[type="file"]');
        preview = url; selectedFile = file; find(".icon-upload-samples").replaceChildren(img, dark);
        find(".icon-upload-preview").hidden = false; find("[data-icon-upload-input]").hidden = true;
        find("[data-icon-back]").hidden = false; find("[data-icon-cancel]").hidden = true;
        find("[data-icon-apply]").disabled = false; status("");
        if (focusFromInput) find("[data-icon-back]").focus();
      } catch (error) { if (!disposed && version === selectionVersion) status(error.message, true); }
      finally { if (version === selectionVersion) remoteAbort = null; }
    }
    find('input[type="file"]').addEventListener("change", event => {
      const file = event.target.files?.[0];
      event.target.value = "";
      void selectImage(file);
    });
    find("[data-icon-custom-file]").addEventListener("change", event => {
      const file = event.target.files?.[0];
      void selectCustomFile(file);
    });
    find("[data-icon-custom-name]").addEventListener("input", () => {
      customRequestToken++; customSaveEnabled();
    });
    root.addEventListener("paste", event => {
      if (disposed || tab !== "upload" || host.allowUpload === false) return;
      const file = clipboardImage(event.clipboardData);
      const url = !file && host.loadUrl ? clipboardUrl(event.clipboardData) : "";
      if (!file && !url) return;
      event.preventDefault(); event.stopPropagation();
      void selectImage(file, url);
    });
    root.addEventListener("keydown", event => {
      if (event.defaultPrevented || event.isComposing || event.keyCode === 229 || busy || disposed || event.altKey || event.ctrlKey || event.metaKey) return;
      if (typeof customOpen !== "undefined" && customOpen && event.key === "Escape") {
        event.preventDefault(); event.stopPropagation(); showCustomForm(false); return;
      }
      if (skinOpen && event.key === "Escape") {
        event.preventDefault(); event.stopPropagation(); setSkinMenu(false, true); return;
      }
      if (event.target === find("[data-icon-skin-toggle]") && ["ArrowDown", "ArrowUp"].includes(event.key)) {
        event.preventDefault(); event.stopPropagation(); setSkinMenu(true, true); return;
      }
      const tones = [...root.querySelectorAll("[data-skin-tone]")];
      if (skinOpen && tones.includes(event.target)) {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault(); event.stopPropagation(); selectSkinTone(Number(event.target.dataset.skinTone)); return;
        }
        if (["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home", "End"].includes(event.key)) {
          event.preventDefault(); event.stopPropagation();
          const index = tones.indexOf(event.target);
          const next = event.key === "Home" ? 0 : event.key === "End" ? tones.length - 1
            : (index + (["ArrowLeft", "ArrowUp"].includes(event.key) ? -1 : 1) + tones.length) % tones.length;
          tones[next].tabIndex = 0; event.target.tabIndex = -1; tones[next].focus(); return;
        }
      }
      if (colorsOpen && event.key === "Escape") {
        event.preventDefault(); event.stopPropagation(); setColorMenu(false, true); return;
      }
      if (event.target === find("[data-icon-color-toggle]") && ["ArrowDown", "ArrowUp"].includes(event.key)) {
        event.preventDefault(); event.stopPropagation(); setColorMenu(true, true); return;
      }
      const colors = [...root.querySelectorAll("[data-color]")];
      if (colorsOpen && colors.includes(event.target)) {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault(); event.stopPropagation(); setColorMenu(false, true); return;
        }
        if (["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home", "End"].includes(event.key)) {
          event.preventDefault(); event.stopPropagation();
          const next = colors[nextIconIndex(colors.map(button => button.getBoundingClientRect()), colors.indexOf(event.target), event.key)];
          selectColor(next.dataset.color); next.focus(); return;
        }
      }
      const categories = [...root.querySelectorAll("[data-icon-category]")];
      if (categories.includes(event.target) && ["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) {
        event.preventDefault(); event.stopPropagation();
        const index = categories.indexOf(event.target);
        const next = event.key === "Home" ? 0 : event.key === "End" ? categories.length - 1
          : (index + (event.key === "ArrowRight" ? 1 : -1) + categories.length) % categories.length;
        group = categories[next].dataset.iconCategory; render(); find(".icon-picker-results").scrollTop = 0;
        categories[next].focus(); return;
      }
      const tabs = [...root.querySelectorAll("[role=tab]")];
      if (tabs.includes(event.target) && ["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) {
        event.preventDefault(); const index = tabs.indexOf(event.target);
        const next = event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1 : (index + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
        tab = tabs[next].dataset.iconTab; group = ""; render(); tabs[next].focus(); return;
      }
      const buttons = [...root.querySelectorAll("[data-icon-value]")];
      const fromSearch = event.target === find('input[type="search"]');
      const index = fromSearch ? buttons.findIndex(button => button.dataset.iconValue === activeValue) : buttons.indexOf(event.target);
      if (fromSearch && ["ArrowUp", "ArrowDown", "Enter"].includes(event.key)) {
        event.preventDefault(); event.stopPropagation();
        if (index < 0) return;
        if (event.key === "Enter") apply(buttons[index].dataset.iconValue);
        else setActive(buttons[nextIconIndex(buttons.map(button => button.getBoundingClientRect()), index, event.key)], false, true);
        return;
      }
      if (fromSearch) return;
      if (index < 0 || !["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home", "End"].includes(event.key)) return;
      event.preventDefault(); event.stopPropagation();
      setActive(buttons[nextIconIndex(buttons.map(button => button.getBoundingClientRect()), index, event.key)], true, true);
    });
    render(); find('input[type="search"]').focus();
    if (host.customEmoji?.list) Promise.resolve().then(() => host.customEmoji.list()).then(items => {
      if (disposed) return;
      customItems = (Array.isArray(items) ? items : []).filter(item =>
        typeof item?.icon === "string" && parse(item.icon).kind === "custom-emoji")
        .map(item => ({value:item.icon,label:item.name,terms:item.name,group:"사용자 지정"}));
      render();
    }).catch(error => { if (!disposed) status(error.message || "사용자 지정 이모지를 불러오지 못했습니다.", true); });
    return {dispose() {disposed = true; selectionVersion++; abort.abort(); remoteAbort?.abort(); if (preview) URL.revokeObjectURL(preview);}};
  }
  return Object.freeze({load, configure, parse, vectorValue, fileValue, fileUrl, content, html, workspace, initial, validateImage, clipboardImage, clipboardUrl, nextIconIndex, readRecent, recordRecent, readSkinTone, saveSkinTone, tonedValue, searchItems, mount});
});

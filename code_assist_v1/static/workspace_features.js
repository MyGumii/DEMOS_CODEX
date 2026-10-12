/* Wiki downloads and shared feedback use the current authenticated account. */
(function () {
  "use strict";
  const statuses = { received: "접수", reviewing: "확인 중", completed: "완료" };
  const kinds = { bug: "버그", improvement: "개선 요청" };
  const pendingRows = new Map();
  let availabilityTimer = null;
  let draft = null;
  let viewSerial = 0;
  let boardState = null;
  let activeBoard = null;

  function leaveBoard() {
    if (!activeBoard) return;
    if (activeBoard.body.isConnected) activeBoard.state.scroll = activeBoard.scroller.scrollTop;
    activeBoard.abort.abort();
    activeBoard = null;
  }

  function boardForActor(reset = false) {
    const owner = typeof State === "object" ? State.currentUser?.id : null;
    if (boardState && boardState.owner !== owner) draft = null;
    if (reset || !boardState || boardState.owner !== owner) boardState = {
      owner, filters: { q: "", field: "all", kind: "", status: "", mine: false },
      cursors: [null], page: 0, scroll: 0, focusId: null,
    };
    return boardState;
  }

  const node = (tag, text, cls) => {
    const el = document.createElement(tag);
    if (text !== undefined) el.textContent = text;
    if (cls) el.className = cls;
    return el;
  };
  const button = (text, action, cls = "ghost") => {
    const el = node("button", text, cls);
    el.type = "button";
    el.addEventListener("click", async event => {
      event.stopPropagation();
      el.disabled = true;
      try { await action(); } catch (error) { toast(error.message, "error"); }
      finally { el.disabled = false; }
    });
    return el;
  };
  async function request(url, options = {}) {
    const response = await authenticatedFetch(url, options);
    if (!response.ok) {
      let value;
      try { value = await response.json(); } catch (_) { /* HTTP failure has no JSON */ }
      throw new Error(value?.error?.message || value?.message || `요청 실패 (${response.status})`);
    }
    return response;
  }
  const json = async (url, options) => (await request(url, options)).json();
  const jsonOptions = (method, body) => ({ method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const wikiPath = sid => `/api/knowledge/v2/spaces/${encodeURIComponent(sid)}`;
  const originalPath = (sid, iid) => `/api/knowledge/v1/spaces/${encodeURIComponent(sid)}/items/${encodeURIComponent(iid)}/source-artifacts`;
  const attachmentPath = (pid, aid) => `/api/code/feedback/${pid}/attachments/${aid}`;
  const when = value => new Date(value).toLocaleString();

  async function download(url, native = false) {
    if (native) {
      // Let the browser stream large ZIPs to its download manager, rather than
      // buffering the complete archive as a JavaScript Blob.
      const frame = node("iframe");
      frame.hidden = true;
      frame.title = "Markdown 다운로드";
      frame.addEventListener("load", () => {
        const text = frame.contentDocument?.body?.textContent?.trim();
        if (text) {
          try { toast(JSON.parse(text)?.error?.message || "다운로드에 실패했습니다.", "error"); }
          catch (_) { toast("다운로드에 실패했습니다. 로그인과 서버 상태를 확인해 주세요.", "error"); }
          frame.remove();
        }
      });
      frame.src = url;
      document.body.append(frame);
      return;
    }
    const response = await request(url);
    const header = response.headers.get("Content-Disposition") || "";
    const encoded = /filename\*=UTF-8''([^;]+)/i.exec(header);
    const plain = /filename="([^"]+)"/.exec(header);
    const name = encoded ? decodeURIComponent(encoded[1]) : (plain?.[1] || "download");
    const blob = await response.blob();
    const objectURL = URL.createObjectURL(blob);
    const link = node("a");
    link.href = objectURL;
    link.download = name;
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(objectURL), 30000);
  }

  function headerControls(sid, openGraph) {
    const group = node("span", undefined, "wiki-header-actions");
    const graph = button("Graph", openGraph);
    graph.title = "Graph 연결 상세";
    const all = button("전체 MD ↓", async () => {
      toast("선택한 지식 공간의 전체 Markdown을 준비합니다.");
      await download(`${wikiPath(sid)}/markdown-export`, true);
    });
    all.title = "선택한 Wiki 전체 문서와 Index · Log를 ZIP으로 다운로드";
    all.dataset.wikiExport = sid;
    group.append(graph, all);
    return group;
  }

  function itemControls(sid, item, refresh) {
    const group = node("span", undefined, "wiki-item-actions");
    const md = button("MD ↓", () => download(`${wikiPath(sid)}/items/${item.id}/markdown-download`));
    md.title = "Markdown 다운로드";
    const original = button("원본 ↓", async () => {
      const result = await json(originalPath(sid, item.id));
      if (!result.items.length) { toast("연결된 원본 파일이 없습니다."); return; }
      if (result.items.length === 1) { await download(result.items[0].download_url); return; }
      const body = node("div", undefined, "wiki-original-list");
      for (const file of result.items) body.append(button(file.original_filename, () => download(file.download_url)));
      Modal.open({ title: "원본 파일 다운로드", body, footButtons: [{ label: "닫기" }] });
    });
    original.title = "연결된 원본 다운로드";
    original.dataset.originalDownload = item.id;
    group.append(md, original);
    if ((item.capabilities || []).includes("edit") && item.origin === "native") {
      const connect = button("⋯", () => linkOriginal(sid, item, refresh));
      connect.title = "기존 Wiki에 원본 파일 연결";
      connect.setAttribute("aria-label", "원본 연결");
      group.append(connect);
    }
    const rows = pendingRows.get(sid) || [];
    rows.push({ id: item.id, button: original });
    pendingRows.set(sid, rows);
    clearTimeout(availabilityTimer);
    availabilityTimer = setTimeout(loadAvailability, 0);
    return group;
  }

  async function loadAvailability() {
    const batches = [...pendingRows.entries()];
    pendingRows.clear();
    for (const [sid, rows] of batches) {
      const connected = rows.filter(r => r.button.isConnected);
      for (let offset = 0; offset < connected.length; offset += 100) {
        const chunk = connected.slice(offset, offset+100);
        try {
          const result = await json(`${wikiPath(sid)}/source-availability`, jsonOptions("POST", { item_ids: chunk.map(r => r.id) }));
          for (const row of chunk) {
            if (!row.button.isConnected) continue;
            row.button.disabled = result.items[row.id] === 0;
            row.button.title = result.items[row.id] === 0 ? "연결된 원본 파일 없음 · ⋯ 메뉴에서 원본 연결" : "원본 파일 다운로드";
          }
        } catch (_) {
          for (const row of chunk) row.button.title = "원본 정보 확인 실패 · 눌러서 다시 확인";
        }
      }
    }
  }

  async function linkOriginal(sid, item, refresh) {
    const detail = await json(`/api/knowledge/v1/spaces/${sid}/items/${item.id}`);
    const body = node("div", undefined, "feedback-form");
    body.append(node("p", "선택한 Wiki의 원본을 연결합니다. 기존 Markdown과 그래프는 유지됩니다."));
    const input = node("input"); input.type = "file"; input.setAttribute("aria-label", "연결할 원본 파일");
    body.append(input);
    let operation = crypto.randomUUID();
    input.addEventListener("change", () => { operation = crypto.randomUUID(); });
    Modal.open({ title: `원본 연결 · ${item.title}`, body, footButtons: [
      { label: "취소" },
      { label: "연결", primary: true, onClick: async () => {
        if (!input.files.length) { toast("원본 파일을 선택해 주세요.", "error"); return false; }
        const form = new FormData();
        form.append("files", input.files[0]);
        form.append("operation_id", operation);
        form.append("expected_revision_id", detail.item.current_revision_id);
        try {
          const result = await json(`${wikiPath(sid)}/items/${item.id}/original-links`, { method: "POST", body: form });
          toast(result.status === "already_linked" ? "이미 연결된 원본입니다." : "원본 파일을 연결했습니다.");
          await refresh?.();
        } catch (error) { toast(error.message, "error"); return false; }
      } },
    ] });
  }

  async function openBoard({ reset = false } = {}) {
    leaveBoard();
    const state = boardForActor(reset);
    const serial = ++viewSerial;
    const body = node("div", undefined, "feedback-board");
    const tools = node("div", undefined, "feedback-toolbar");
    tools.append(node("span", "로그인한 모든 사용자가 볼 수 있습니다."), button(draft ? "작성 중인 글 이어쓰기" : "작성", () => editPost(), "primary"));

    const filters = node("form", undefined, "feedback-filters");
    const searchRow = node("div", undefined, "feedback-search-row");
    function selector(label, choices, value) {
      const select = node("select"); select.setAttribute("aria-label", label);
      for (const [id, name] of Object.entries(choices)) {
        const option = node("option", name); option.value = id; select.append(option);
      }
      select.value = value;
      return select;
    }
    const field = selector("검색 범위", { all: "전체", title: "제목", body: "내용", requester: "요청자" }, state.filters.field);
    const query = node("input"); query.type = "search"; query.maxLength = 200;
    query.placeholder = "제목 · 내용 · 요청자 이름/ID 검색";
    query.setAttribute("aria-label", "게시글 검색어"); query.value = state.filters.q;
    const search = node("button", "검색", "primary"); search.type = "submit";
    searchRow.append(field, query, search, button("초기화", () => {
      query.value = ""; field.value = "all"; kind.value = ""; status.value = ""; mine.checked = false;
      return applyFilters();
    }));
    const filterRow = node("div", undefined, "feedback-filter-row");
    const kind = selector("요청 유형 필터", { "": "모든 유형", ...kinds }, state.filters.kind);
    const status = selector("처리 상태 필터", { "": "모든 상태", ...statuses }, state.filters.status);
    const mineLabel = node("label", undefined, "feedback-mine");
    const mine = node("input"); mine.type = "checkbox"; mine.checked = state.filters.mine;
    mineLabel.append(mine, node("span", "내가 쓴 글만"));
    filterRow.append(kind, status, mineLabel);
    filters.append(searchRow, filterRow);

    const table = node("div", undefined, "feedback-table");
    const headings = node("div", undefined, "feedback-columns");
    for (const label of ["제목", "요청자", "상태", "작성일"]) headings.append(node("span", label));
    const list = node("div", "게시글을 불러오는 중…", "feedback-list");
    list.setAttribute("aria-label", "요청 게시글 목록");
    table.append(headings, list);
    body.append(tools, filters, table);
    Modal.open({ title: "버그·개선 요청", body, footButtons: [{ label: "닫기" }] });

    const scroller = document.getElementById("modalBody");
    const abort = new AbortController();
    activeBoard = { body, scroller, state, abort };
    scroller.addEventListener("scroll", () => {
      if (body.isConnected) state.scroll = scroller.scrollTop;
    }, { signal: abort.signal });
    const pager = node("div", undefined, "feedback-pagination");
    const previous = node("button", "← 이전"), next = node("button", "다음 →");
    previous.type = next.type = "button";
    const pageInfo = node("span", "불러오는 중…", "feedback-page-info");
    pageInfo.setAttribute("aria-live", "polite");
    pager.append(previous, pageInfo, next);
    document.getElementById("modalFoot").prepend(pager);

    let loadSerial = 0, busy = false, nextCursor = null;
    function navigation() {
      previous.disabled = busy || state.page === 0;
      next.disabled = busy || !nextCursor;
    }
    async function loadPage(page, scroll = 0, focusId = null) {
      const generation = ++loadSerial;
      const cursor = state.cursors[page];
      const parameters = new URLSearchParams({ ...state.filters, mine: state.filters.mine ? "1" : "0" });
      if (cursor) parameters.set("cursor", cursor);
      busy = true; navigation(); list.setAttribute("aria-busy", "true");
      list.replaceChildren(node("p", "게시글을 불러오는 중…"));
      try {
        const result = await json(`/api/code/feedback?${parameters}`);
        if (generation !== loadSerial || serial !== viewSerial || !body.isConnected) return;
        if (!result.items.length && page > 0) {
          // The final post on a page may have been deleted or filtered out by
          // an edit. Move to the nearest remaining page instead of a dead end.
          state.cursors.length = page;
          await loadPage(page - 1);
          return;
        }
        state.page = page; state.scroll = scroll; state.focusId = focusId;
        state.cursors.length = page + 1;
        nextCursor = result.next_cursor;
        if (nextCursor) state.cursors.push(nextCursor);
        list.replaceChildren();
        if (!result.items.length) list.append(node("p", "표시할 게시글이 없습니다. 검색·필터 조건을 확인하거나 새 요청을 작성해 주세요."));
        for (const post of result.items) {
          const row = button("", () => {
            state.focusId = post.id; state.scroll = scroller.scrollTop;
            return openPost(post.id);
          }, "feedback-post-row");
          row.dataset.feedbackPostId = post.id;
          const title = node("span", undefined, "feedback-row-title");
          title.append(node("small", kinds[post.kind], "feedback-kind"), node("strong", post.title));
          title.title = post.title;
          if (post.attachments.length) title.append(node("small", `첨부 ${post.attachments.length}`, "feedback-attachment-count"));
          const requester = `${post.author_name} (${post.author_id})`;
          const author = node("span", requester, "feedback-row-author"); author.title = requester;
          const date = node("time", new Date(post.created_at).toLocaleDateString("ko-KR"));
          date.dateTime = new Date(post.created_at).toISOString(); date.title = when(post.created_at);
          row.append(title, author, node("span", statuses[post.status], `feedback-status feedback-status-${post.status}`), date);
          list.append(row);
        }
        pageInfo.textContent = `${page + 1}페이지 · ${result.items.length}개 / 페이지당 30개`;
        requestAnimationFrame(() => {
          if (generation !== loadSerial || serial !== viewSerial || !body.isConnected) return;
          const focused = [...list.querySelectorAll("[data-feedback-post-id]")].find(row => row.dataset.feedbackPostId === focusId);
          focused?.focus({ preventScroll: true });
          scroller.scrollTop = scroll;
        });
      } catch (error) {
        if (generation !== loadSerial || serial !== viewSerial || !body.isConnected) return;
        nextCursor = null;
        pageInfo.textContent = "목록 조회 실패";
        list.replaceChildren(node("p", error.message), button("다시 시도", () => loadPage(page, scroll, focusId)));
      } finally {
        if (generation === loadSerial && serial === viewSerial && body.isConnected) {
          busy = false; list.removeAttribute("aria-busy"); navigation();
        }
      }
    }
    function applyFilters() {
      state.filters = { q: query.value.trim(), field: field.value, kind: kind.value, status: status.value, mine: mine.checked };
      state.cursors = [null]; state.page = 0; state.scroll = 0; state.focusId = null;
      return loadPage(0);
    }
    filters.addEventListener("submit", event => { event.preventDefault(); applyFilters(); });
    [field, kind, status, mine].forEach(control => control.addEventListener("change", applyFilters));
    previous.addEventListener("click", () => { if (!busy && state.page > 0) loadPage(state.page - 1); });
    next.addEventListener("click", () => { if (!busy && nextCursor) loadPage(state.page + 1); });
    await loadPage(state.page, state.scroll, state.focusId);
  }

  async function openPost(id) {
    leaveBoard();
    const serial = ++viewSerial;
    const post = await json(`/api/code/feedback/${id}`);
    if (serial !== viewSerial) return;
    const body = node("div", undefined, "feedback-detail");
    body.append(button("← 게시판으로", openBoard, "ghost feedback-back"));
    body.append(node("p", `${kinds[post.kind]} · ${post.author_name} · ${when(post.created_at)} · ${statuses[post.status]}`, "feedback-meta"));
    if (post.updated_at !== post.created_at) body.append(node("small", `수정: ${when(post.updated_at)}`));
    body.append(node("div", post.body, "feedback-content"));
    const assets = node("div", undefined, "feedback-assets");
    body.append(assets);
    for (const asset of post.attachments) {
      const card = node("div", undefined, "feedback-asset");
      const path = attachmentPath(id, asset.id);
      card.append(button(`${asset.filename} ↓`, () => download(path)));
      if (["image/png", "image/jpeg", "image/gif", "image/webp", "image/bmp"].includes(asset.media_type)) {
        // The authorized attachment response is still downloaded as an attachment.
        // A fetched blob is used only for this in-dialog raster preview.
        request(path).then(r => r.blob()).then(blob => {
          if (!card.isConnected) return;
          const image = node("img");
          const url = URL.createObjectURL(blob);
          image.src = url; image.alt = asset.filename;
          image.addEventListener("click", () => image.classList.toggle("expanded"));
          image.addEventListener("load", () => URL.revokeObjectURL(url), { once: true });
          card.prepend(image);
        }).catch(error => card.append(node("small", error.message)));
      }
      assets.append(card);
    }
    const actions = node("div", undefined, "feedback-toolbar");
    if (post.can_edit) {
      const deleteOperation = crypto.randomUUID();
      actions.append(button("수정", () => editPost(post)), button("삭제", async () => {
        if (!window.confirm("이 게시글을 삭제하시겠습니까?")) return;
        await json(`/api/code/feedback/${id}`, jsonOptions("DELETE", { operation_id: deleteOperation, expected_version: post.row_version }));
        toast("게시글을 삭제했습니다.");
        await openBoard();
      }, "danger"));
    }
    if (post.can_change_status) {
      let statusOperation = crypto.randomUUID();
      const select = node("select"); select.setAttribute("aria-label", "처리 상태");
      for (const [value, label] of Object.entries(statuses)) { const option = node("option", label); option.value=value; select.append(option); }
      select.value=post.status;
      select.addEventListener("change", () => { statusOperation = crypto.randomUUID(); });
      actions.append(select, button("상태 저장", async () => {
        await json(`/api/code/feedback/${id}/status`, jsonOptions("PATCH", { operation_id: statusOperation, expected_version: post.row_version, status: select.value }));
        await openPost(id);
      }));
    }
    if (actions.children.length) body.append(actions);
    Modal.open({ title: post.title, body, footButtons: [
      { label: "← 게시판으로", onClick: async () => { await openBoard(); return false; } },
      { label: "닫기" },
    ] });
  }

  function editPost(post = null) {
    leaveBoard();
    ++viewSerial;
    if (post && draft && draft.id !== post.id && !window.confirm("작성 중인 초안을 바꾸시겠습니까?")) return;
    if (post || !draft) draft = {
      id: post?.id || null, version: post?.row_version, title: post?.title || "", kind: post?.kind || "bug",
      body: post?.body || "", kept: [...(post?.attachments || [])], files: [], operation: crypto.randomUUID(),
    };
    const current = draft;
    const form = node("div", undefined, "feedback-form");
    const title = node("input"); title.value=current.title; title.maxLength=200; title.placeholder="제목";
    title.setAttribute("aria-label", "요청 제목");
    const kind = node("select"); kind.setAttribute("aria-label", "요청 유형");
    for (const [value,label] of Object.entries(kinds)) { const o=node("option",label);o.value=value;kind.append(o); }
    kind.value=current.kind;
    const content=node("textarea"); content.value=current.body; content.rows=10; content.maxLength=50000;
    content.placeholder="어떤 상황에서 발생했는지, 원하는 동작은 무엇인지 작성해 주세요. 이미지를 붙여넣을 수 있습니다.";
    content.setAttribute("aria-label", "요청 내용");
    const changed=()=>{current.title=title.value;current.kind=kind.value;current.body=content.value;current.operation=crypto.randomUUID();};
    [title,kind,content].forEach(el=>el.addEventListener("input",changed));
    const picker=node("input");picker.type="file";picker.multiple=true;picker.hidden=true;
    const drop=node("div",undefined,"feedback-drop");drop.tabIndex=0;
    drop.append(node("span","파일을 끌어 놓거나 이미지를 붙여넣으세요."),button("파일 선택",()=>picker.click()),picker);
    const previews=node("div",undefined,"feedback-assets");
    const urls=[];
    function renderFiles() {
      urls.splice(0).forEach(url=>URL.revokeObjectURL(url));
      previews.replaceChildren();
      for(const existing of current.kept) previews.append(button(`${existing.filename} · 제거`,()=>{
        current.kept=current.kept.filter(a=>a.id!==existing.id);current.operation=crypto.randomUUID();renderFiles();
      }));
      current.files.forEach((file,index)=>{
        const card=node("div",undefined,"feedback-asset");
        if(/^image\/(png|jpeg|gif|webp|bmp)$/.test(file.type)) {
          const img=node("img");const url=URL.createObjectURL(file);urls.push(url);img.src=url;img.alt=file.name;card.append(img);
        }
        card.append(button(`${file.name} · 제거`,()=>{current.files.splice(index,1);current.operation=crypto.randomUUID();renderFiles();}));previews.append(card);
      });
    }
    function addFiles(files) {
      for(const file of files) if(!current.files.some(f=>f.name===file.name&&f.size===file.size&&f.lastModified===file.lastModified)) current.files.push(file);
      current.operation=crypto.randomUUID();renderFiles();
    }
    picker.addEventListener("change",()=>{addFiles([...picker.files]);picker.value="";});
    form.addEventListener("paste",event=>{
      const images=[...(event.clipboardData?.items||[])].filter(i=>i.kind==="file"&&i.type.startsWith("image/"));
      if(!images.length)return;event.preventDefault();event.stopPropagation();
      addFiles(images.map(i=>i.getAsFile()).filter(Boolean));
    });
    ["dragenter","dragover"].forEach(type=>form.addEventListener(type,event=>{event.preventDefault();event.stopPropagation();drop.classList.add("dragging");}));
    form.addEventListener("dragleave",()=>drop.classList.remove("dragging"));
    form.addEventListener("drop",event=>{event.preventDefault();event.stopPropagation();drop.classList.remove("dragging");addFiles([...(event.dataTransfer?.files||[])]);});
    form.append(title,kind,content,drop,previews);
    const actions=node("div",undefined,"feedback-toolbar");
    actions.append(button("목록으로 · 초안 유지",openBoard),button("초안 버리기",async()=>{if(window.confirm("작성 중인 내용을 버릴까요?")){draft=null;await openBoard();}}));
    form.append(actions);renderFiles();
    Modal.open({title:current.id?"요청 수정":"요청 작성",body:form,footButtons:[
      {label:"작성 완료",primary:true,onClick:async()=>{
        if(!current.title.trim()||!current.body.trim()){toast("제목과 내용을 입력해 주세요.","error");return false;}
        const data=new FormData();
        for(const key of ["title","kind","body"])data.append(key,current[key]);
        data.append("operation_id",current.operation);data.append("keep_ids",JSON.stringify(current.kept.map(a=>a.id)));
        if(current.id)data.append("expected_version",current.version);
        current.files.forEach(file=>data.append("files",file));
        try {
          const result=await json("/api/code/feedback"+(current.id?`/${current.id}`:""),{method:current.id?"PUT":"POST",body:data});
          urls.splice(0).forEach(url=>URL.revokeObjectURL(url));draft=null;
          if(current.id)await openPost(result.id);else await openBoard({ reset: true });
          toast(current.id?"요청을 수정했습니다.":"요청을 등록했습니다.");
        }catch(error){toast(error.message,"error");}
        return false;
      }},
    ]});
    title.focus();
  }
  window.WorkspaceFeatures={headerControls,itemControls,download,openBoard};
  document.getElementById("feedbackBoardBtn")?.addEventListener("click",()=>openBoard().catch(error=>toast(error.message,"error")));
})();

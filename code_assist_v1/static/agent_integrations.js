/* Delegated agent connections and evidence review under the signed-in account. */
(function () {
  "use strict";
  const base = "/api/integrations/v1";
  const states = {queued:"정제 대기", processing:"정제 중", review:"검토 필요", ready:"검토 승인", publishing:"등록 중", published:"등록 제출 완료", rejected:"보류", failed:"처리 실패"};
  const evidenceNames = {approved:"실행 승인", user_reported_complete:"직접 처리했다고 확인", cancelled:"취소", outcome_reported:"결과 보고", note:"메모"};
  let dialog, body, lastFocus, viewSerial=0;
  const pending=new Map();
  const spaceNames=new Map();
  const modeName=value=>value==="personal_shared"?"내 지식+공유 지식":"내 지식";
  const targetName=value=>({personal:"개인",organization:"조직",both:"개인+파트"}[value]||value);
  const node = (tag, value, cls) => { const el = document.createElement(tag); if(value !== undefined) el.textContent=value; if(cls) el.className=cls; return el; };
  function button(label, action) {
    const el=node("button",label); el.type="button";
    el.addEventListener("click",async()=>{ el.disabled=true; try { await action(); } catch(e) { toast(e.message,"error"); } finally { el.disabled=false; }});
    return el;
  }
  async function api(path, data) {
    let key;
    if(data?.operation_id) {
      const {operation_id,...payload}=data;key=JSON.stringify([path,payload]);
      if(!pending.has(key))pending.set(key,operation_id);
      data={...payload,operation_id:pending.get(key)};
    }
    const response=await authenticatedFetch(base+path, data === undefined ? {} : {method:"POST", headers:{"Content-Type":"application/json"},body:JSON.stringify(data)});
    let result; try {result=await response.json();} catch (_) {throw new Error("서버 응답을 확인할 수 없습니다.");}
    if(!response.ok) throw new Error(result?.error?.message || "연동 요청에 실패했습니다.");
    if(key)pending.delete(key);
    return result;
  }
  const op=()=>crypto.randomUUID();
  function field(parent,label,type="text",value="") {
    const wrap=node("label",label), input=node(type==="textarea"?"textarea":"input");
    input.setAttribute("aria-label",label);
    if(type!=="textarea") input.type=type;
    input.value=value; wrap.append(input); parent.append(wrap); return input;
  }
  function select(parent,label,items) {
    const wrap=node("label",label), el=node("select");
    el.setAttribute("aria-label",label);
    for(const [id,title] of items) { const option=node("option",title);option.value=id;el.append(option); }
    wrap.append(el);parent.append(wrap);return el;
  }
  function checks(parent,label,items,selected=[]) {
    const group=node("fieldset",undefined,"aik-wikis");group.append(node("legend",label));
    const inputs=items.map(([id,title])=>{const input=field(group,title,"checkbox");input.value=id;input.checked=selected.includes(id);return input;});
    parent.append(group);return ()=>inputs.filter(input=>input.checked).map(input=>input.value);
  }
  function section(title) {const el=node("section",undefined,"aik-section");el.append(node("h3",title));body.append(el);return el;}
  function clear() {body.replaceChildren();}
  function show() {
    if(dialog) {dialog.showModal(); return;}
    dialog=node("dialog",undefined,"aik-dialog");
    const header=node("header");header.append(node("h2","에이전트 지식 연결"),button("닫기",()=>dialog.close()));
    const nav=node("nav");nav.append(button("연결 설정",connections),button("작업·근거 검토",()=>runs()),button("직접 자료 제출",submission));
    body=node("div",undefined,"aik-body");dialog.append(header,nav,body);document.body.append(dialog);
    dialog.addEventListener("close",()=>{viewSerial++;pending.clear();body.replaceChildren();dialog.remove();dialog=null;lastFocus?.focus();});
    dialog.showModal();
  }
  async function allWikis() {
    const items=[];let cursor;
    do {const page=await api("/wikis"+(cursor?"?cursor="+encodeURIComponent(cursor):""));items.push(...page.items);cursor=page.next_cursor;} while(cursor);
    items.forEach(w=>spaceNames.set(w.space_id,w.display_name));
    return items;
  }
  async function connections() {
    const serial=++viewSerial;
    const [status, available, grants, targets, outlook] = await Promise.all([api("/status"),allWikis(),api("/connections"),api("/notion/targets").catch(error=>({items:[],error:error.message})),api("/outlook/status").catch(error=>({enabled:false,error:error.message}))]);
    if(!dialog?.open||serial!==viewSerial)return;
    clear();
    const sets=status.knowledge_sets_version>=2, organizations=status.organization_views||[];
    organizations.forEach(s=>spaceNames.set(s.space_id,(s.org_path||[]).join(" / ")||s.display_name));
    body.append(node("p","Hermes가 참고할 내 지식과 공유 범위, 결과를 등록할 대상을 선택합니다. 검색할 때 관련 발췌만 가져오며, Second Brain이 원문 근거를 연결해 지식 후보를 작성합니다."));
    const form=section("새 연결");
    const name=field(form,"연결 이름","text","Hermes");
    const options=available.map(w=>[w.space_id,w.display_name+(w.is_default?" · 기본":"")]);
    const read=sets?null:select(form,"참고할 Wiki",options);
    const selectedRead=sets?checks(form,"참고할 개인 Wiki · 복수 선택",options,[options[0]?.[0]]):()=>[read.value];
    const readMode=sets?select(form,"조회 모드",[["personal","내 지식"],["personal_shared","내 지식+공유 지식"]]):null;
    const shared=sets?select(form,"공유 지식 범위",organizations.map(s=>[s.space_id,(s.org_path||[]).join(" / ")+({part:" · 파트",team:" · 하위 파트 포함",responsibility:" · 하위 팀·파트 포함"}[s.kind]||"")])):null;
    if(sets) {
      readMode.options[1].disabled=!organizations.length;
      const toggle=()=>{shared.closest("label").hidden=readMode.value!=="personal_shared";};toggle();readMode.addEventListener("change",toggle);
      form.append(node("p","공유 지식은 선택한 조직 뷰의 권한 범위만 포함합니다. 팀·담당 뷰에는 하위 조직 지식이 포함되며 개인 Wiki는 포함되지 않습니다.","aik-muted"));
    }
    const write=select(form,"지식을 등록할 Wiki",options);
    const extraWrite=sets?checks(form,"추가로 등록을 허용할 개인 Wiki",options):()=>[];
    const writable=organizations.filter(s=>s.can_publish);
    const allowOrg=sets?field(form,"본인 조직에 등록 허용","checkbox"):null;
    const writeOrg=sets?select(form,"등록할 조직",writable.map(s=>[s.space_id,spaceNames.get(s.space_id)])):null;
    const allowBoth=sets?field(form,"개인+파트 동시 등록 허용","checkbox"):null;
    if(sets) {
      allowOrg.disabled=!writable.length;
      const toggle=()=>{
        writeOrg.closest("label").hidden=!allowOrg.checked;
        const part=allowOrg.checked&&writable.find(s=>s.space_id===writeOrg.value)?.kind==="part";
        allowBoth.closest("label").hidden=!part;allowBoth.disabled=!part;if(!part)allowBoth.checked=false;
      };toggle();allowOrg.addEventListener("change",toggle);writeOrg.addEventListener("change",toggle);
      form.append(node("p","등록 대상은 자료 제출 시 허용된 범위에서 선택합니다. 팀장·담당장은 본인의 팀·담당에 등록하며, 개인+파트는 두 개의 독립된 지식으로 생성됩니다.","aik-muted"));
    }
    const days=field(form,"유효 기간 (일)","number","30");days.min="1";days.max="365";
    const renew=field(form,"사용 중인 토큰 자동 연장","checkbox");
    form.append(node("p","자동 연장을 켜면 Hermes의 성공한 API 사용 시점부터 위 기간만큼 유효 기간이 갱신됩니다. 기간 동안 사용하지 않아 만료되면 새 연결이 필요합니다.","aik-muted"));
    const notion=select(form,"Notion 기록 위치",[["","사용 안 함"],...targets.items.map(t=>[t.id,t.path||t.title])]);
    if(status.notion_configured)form.append(node("p","상위 / 하위 페이지 경로로 구분합니다. 선택한 위치 바로 아래에 작업별 기록 페이지를 만듭니다.","aik-muted"));
    if(!status.notion_configured) form.append(node("p","이 서버에는 Notion 저장소가 아직 연결되지 않았습니다. Wiki 연결은 사용할 수 있습니다.","aik-muted"));
    if(targets.error)form.append(node("p","Notion 위치를 불러오지 못했습니다: "+targets.error,"aik-warning"));
    const mail=field(form,"Hermes의 서버 Outlook 읽기·회신 관측 허용","checkbox");mail.disabled=!status.mail_connector_enabled||!outlook.authenticated;
    form.append(node("p","Hermes 자체 메일 도구를 사용하면 이 권한은 필요하지 않습니다. Second Brain의 Outlook을 Hermes도 사용하려는 경우에만 켜세요.","aik-muted"));
    const auto=field(form,"원문 인용 기록 자동 등록 허용","checkbox");
    form.append(node("p","자동 등록은 출처가 표시된 원문 인용에만 적용됩니다. 모델의 요약·해석과 실행 결과는 검토 대상입니다.","aik-muted"));
    form.append(button("연결 만들기",async()=>{
      const scopes=["knowledge:read","knowledge:ask","results:write"];
      if(notion.value)scopes.push("notion:write");
      if(mail.checked)scopes.push("mail:read");
      const personal=selectedRead();if(!personal.length)throw new Error("참고할 개인 Wiki를 하나 이상 선택해 주세요.");
      const setConfig=sets?{knowledge_sets_version:2,read_mode:readMode.value,personal_space_ids:personal,
        shared_space_id:readMode.value==="personal_shared"?shared.value:"",write_personal_space_ids:[...new Set([write.value,...extraWrite()])],
        write_org_space_id:allowOrg.checked?writeOrg.value:"",write_modes:["personal",...(allowOrg.checked?["organization"]:[]),...(allowBoth.checked?["both"]:[])]}:{};
      const result=await api("/connections",{operation_id:op(),name:name.value,read_space_id:personal[0],write_space_id:write.value,expires_days:Number(days.value),auto_renew:renew.checked,scopes,auto_publish_sources:auto.checked,notion_parent_id:notion.value,...setConfig});
      clear();const issued=section("연결이 생성되었습니다");
      issued.append(node("p","토큰을 저장해 Hermes 연결 설정에 넣어 주세요. 이후 연결 설정의 내 연결에서 토큰 보기로 다시 확인할 수 있습니다."));
      const token=field(issued,"에이전트 토큰","textarea",result.token);token.readOnly=true;token.spellcheck=false;
      issued.append(node("p","Second Brain 주소: "+location.origin));
      issued.append(button("토큰 복사",async()=>{await navigator.clipboard.writeText(result.token);toast("토큰을 복사했습니다.");}),button("연결 목록",connections));
    }));
    const mailbox=section("Second Brain의 Outlook 연결");
    mailbox.append(node("p","이 서버에 Microsoft 계정을 연결합니다. 연결만으로 메일 수집·발송이나 자동화를 시작하지 않습니다."));
    if(outlook.authenticated) {
      mailbox.append(node("p","연결됨 · "+(outlook.account?.username||outlook.account?.name||"Microsoft 계정")));
      mailbox.append(button("Outlook 연결 해제",async()=>{await api("/outlook/disconnect",{});await connections();}));
    } else if(outlook.enabled && outlook.configuration?.configured && outlook.redirect_matches) {
      mailbox.append(button("Microsoft 계정 연결",async()=>{const result=await api("/outlook/connect",{});location.assign(result.authorization_url);}));
    } else {
      mailbox.append(node("p",outlook.enabled?"Microsoft 앱 서버 설정이 필요합니다. 설정이 완료되면 Microsoft 계정 연결 버튼이 표시됩니다.":"서버의 Outlook 연결 기능이 비활성 상태입니다.","aik-muted"));
      if(outlook.enabled) {
        const setup=node("details");setup.append(node("summary","필요한 연결 설정"));
        for(const missing of outlook.configuration?.missing||[])setup.append(node("p",missing));
        setup.append(node("p","리디렉션 주소: "+outlook.expected_redirect_uri));mailbox.append(setup);
      }
    }
    const list=section("내 연결");
    if(!grants.items.length) list.append(node("p","아직 생성한 연결이 없습니다."));
    for(const grant of grants.items) {
      const row=node("article");const label=id=>spaceNames.get(id)||"사용할 수 없는 공간";
      row.append(node("strong",grant.name));
      if(grant.knowledge_sets_version===2) {
        row.append(node("p",modeName(grant.read_mode)+" · "+grant.personal_space_ids.map(label).join(", ")+(grant.shared_space_id?" + "+label(grant.shared_space_id):"")));
        row.append(node("p","등록 허용: "+grant.write_personal_space_ids.map(label).join(", ")+(grant.write_org_space_id?" / "+label(grant.write_org_space_id):"")+" · "+grant.write_modes.map(targetName).join(", ")));
      } else row.append(node("p","기존 단일 Wiki 연결 · "+label(grant.read_space_id)+" → "+label(grant.write_space_id)));
      row.append(node("p",grant.revoked_at?"폐기됨":"만료: "+new Date(grant.expires_at).toLocaleString()));
      row.append(node("p",grant.auto_renew?"자동 연장 켜짐 · 성공한 API 사용 후 "+grant.expires_days+"일":"고정 유효 기간"));
      if(status.connection_token_reveal && !grant.revoked_at && grant.expires_at>Date.now()) {
        const tokenBox=node("div");
        const reveal=button("토큰 보기",async()=>{
          if(tokenBox.childElementCount) {
            tokenBox.querySelectorAll("textarea").forEach(el=>{el.value="";});
            tokenBox.replaceChildren();reveal.textContent="토큰 보기";reveal.setAttribute("aria-expanded","false");return;
          }
          const result=await api(`/connections/${grant.id}/token`,{operation_id:op()});
          if(!dialog?.open||serial!==viewSerial||!row.isConnected)return;
          const token=field(tokenBox,"에이전트 토큰","textarea",result.token);
          token.readOnly=true;token.spellcheck=false;token.autocomplete="off";
          tokenBox.append(button("토큰 복사",async()=>{await navigator.clipboard.writeText(token.value);toast("토큰을 복사했습니다.");}));
          reveal.textContent="토큰 숨기기";reveal.setAttribute("aria-expanded","true");
        });
        reveal.setAttribute("aria-expanded","false");row.append(reveal,tokenBox);
      }
      if(!grant.revoked_at && grant.expires_at>Date.now())row.append(button(grant.auto_renew?"자동 연장 끄기":"자동 연장 켜기",async()=>{
        await api(`/connections/${grant.id}/renewal`,{operation_id:op(),expected_version:grant.row_version,auto_renew:!grant.auto_renew});await connections();
      }));
      if(!grant.revoked_at) row.append(button("연결 폐기",async()=>{await api(`/connections/${grant.id}/revoke`,{operation_id:op()});await connections();}));
      list.append(row);
    }
  }
  async function runs(cursor) {
    const serial=++viewSerial;
    const result=await api("/runs"+(cursor?"?cursor="+encodeURIComponent(cursor):""));
    if(!dialog?.open||serial!==viewSerial)return;
    clear();const list=section("작업과 지식 후보");list.append(button("새로고침",()=>runs()));
    if(!result.items.length)list.append(node("p","제출된 작업이 없습니다. Hermes에서 결과를 보내거나 자료를 직접 제출해 보세요."));
    for(const run of result.items) {
      const row=node("article");row.append(button(run.submission.title,()=>detail(run.id)),node("span",states[run.state]||run.state),node("p",run.submission.kind+" · "+new Date(run.created_at).toLocaleString()));list.append(row);
    }
    if(result.next_cursor)list.append(button("이전 작업 더 보기",()=>runs(result.next_cursor)));
  }
  async function detail(id) {
    const serial=++viewSerial;
    const run=await api("/runs/"+id);if(!dialog?.open||serial!==viewSerial)return;
    clear();const summary=section(run.submission.title);
    summary.append(node("p",states[run.state]),node("p",run.submission.result||"별도 제안 없음"),button("새로고침",()=>detail(id)));
    if(run.submission.publication_target) {
      const target=run.submission.publication_target;
      summary.append(node("p","조회: "+modeName(run.submission.read_mode)+" · 등록: "+targetName(target.mode)+" · "+[target.personal_space_id,target.organization_space_id].filter(Boolean).map(id=>spaceNames.get(id)||"선택한 공간").join(" / ")));
    }
    if(run.error_code)summary.append(node("p","오류: "+run.error_code));
    if(run.submission.followup_of)summary.append(button("선행 작업 보기",()=>detail(run.submission.followup_of)));
    if(["published","rejected"].includes(run.state))summary.append(button("후속 결과 기록 만들기",async()=>{
      const sources=run.submission.sources.map(s=>Object.fromEntries(["id","kind","title","text","uri","external_id","revision"].map(k=>[k,s[k]||""])));
      const actions=run.submission.actions.map(a=>({description:a.description,completion_condition:a.completion_condition,...(a.source_id?{source_id:a.source_id}:{})}));
      const next=await api(`/connections/${run.connection_id}/runs`,{operation_id:op(),kind:run.submission.kind,title:run.submission.title,sources,actions,followup_of:id,result:"선행 작업의 후속 결과 확인이 필요합니다."});
      await detail(next.id);
    }));
    if(["queued","failed"].includes(run.state))summary.append(button("Second Brain으로 정제",async()=>{await api(`/runs/${id}/process`,{operation_id:op()});await detail(id);}));
    const sources=section("제출 원본");for(const source of run.submission.sources) {const el=node("details");el.append(node("summary",source.title),node("pre",source.text));sources.append(el);}
    const actions=section("액션과 실행 근거");
    if(!run.submission.actions.length)actions.append(node("p","등록된 액션이 없습니다."));
    for(const action of run.submission.actions) {
      const row=node("article");row.append(node("strong",action.description),node("p","완료 기준: "+action.completion_condition));
      const evidence=run.evidence.filter(e=>e.action_id===action.id);
      row.append(node("p",evidence.length?"확인 기록 (승인은 실행 완료를 의미하지 않습니다)":"실행 여부 미확인"));
      for(const e of evidence)row.append(node("p",(evidenceNames[e.kind]||e.kind)+": "+e.body));
      if(!["processing","publishing","published"].includes(run.state)) {
        const kind=select(row,"확인 종류",Object.entries(evidenceNames));const content=field(row,"확인 내용·근거","textarea");
        row.append(button("근거 추가 후 다시 정제",async()=>{await api(`/runs/${id}/evidence`,{operation_id:op(),expected_version:run.row_version,action_id:action.id,kind:kind.value,body:content.value});await detail(id);}));
      }
      actions.append(row);
    }
    if(run.draft) {
      const draft=section("Second Brain 지식 후보");draft.append(node("p",run.draft.summary));
      for(const claim of run.draft.claims) {const row=node("article");row.append(node("p",claim.category+" · "+claim.text));for(const ref of claim.evidence) row.append(node("blockquote",ref.quote+" ("+ref.source_id+")"));draft.append(row);}
      for(const warning of [...run.draft.blockers,...run.draft.warnings])draft.append(node("p",warning,"aik-warning"));
      if(run.state==="review") {
        draft.append(node("p","원문이 주장을 뒷받침하는지, 실제 처리와 권장 사항이 구분되어 있는지 확인해 주세요."));
        if(!run.draft.blockers.length)draft.append(button("이 버전 검토 승인",async()=>{await api(`/runs/${id}/review`,{operation_id:op(),expected_version:run.row_version,draft_hash:run.draft_hash,decision:"approve"});await detail(id);}));
        draft.append(button("후보 보류",async()=>{await api(`/runs/${id}/review`,{operation_id:op(),expected_version:run.row_version,draft_hash:run.draft_hash,decision:"reject"});await detail(id);}));
      }
      if(["ready","publishing"].includes(run.state))draft.append(button("지정 Wiki에 등록",async()=>{await api(`/runs/${id}/publish`,{operation_id:op()});await detail(id);}));
    }
    if(run.publication?.results)summary.append(node("p","Wiki 등록이 접수되었습니다. 지식탭에서 정제·공개 상태를 확인할 수 있습니다."));
    summary.append(button("현재 기록을 Notion에 저장",async()=>{await api(`/runs/${id}/notion`,{operation_id:op()});toast("Notion에 현재 버전을 기록했습니다.");await detail(id);}));
    if(run.notion)summary.append(node("p","Notion 기록 버전: "+run.notion.snapshot_version));
    if(["processing","publishing"].includes(run.state))setTimeout(()=>{
      if(dialog?.open&&serial===viewSerial)detail(id).catch(error=>toast(error.message,"error"));
    },3000);
  }
  async function submission() {
    const serial=++viewSerial;
    const grants=(await api("/connections")).items.filter(c=>!c.revoked_at&&c.expires_at>Date.now());
    if(!dialog?.open||serial!==viewSerial)return;clear();const form=section("정제할 자료 제출");
    if(!grants.length) {form.append(node("p","먼저 참고·등록 Wiki를 선택해 연결을 만들어 주세요."));return;}
    const connection=select(form,"연결",grants.map(c=>[c.id,c.name])),title=field(form,"제목"),content=field(form,"원본 자료","textarea"),result=field(form,"에이전트 제안 또는 작업 결과","textarea");
    const targetBox=node("div");form.append(targetBox);let selectedTarget=()=>({});
    const configure=()=>{
      targetBox.replaceChildren();const grant=grants.find(c=>c.id===connection.value);
      selectedTarget=()=>({});if(grant.knowledge_sets_version!==2)return;
      const queryMode=select(targetBox,"자료 정제 시 조회 모드",[["personal","내 지식"],...(grant.read_mode==="personal_shared"?[["personal_shared","내 지식+공유 지식"]]:[])]);queryMode.value=grant.read_mode;
      const mode=select(targetBox,"등록 대상",grant.write_modes.map(m=>[m,targetName(m)]));
      const personal=select(targetBox,"등록할 개인 Wiki",grant.write_personal_space_ids.map(id=>[id,spaceNames.get(id)||"허용된 개인 Wiki"]));personal.value=grant.write_space_id;
      const org=node("p","등록 조직: "+(spaceNames.get(grant.write_org_space_id)||"선택한 조직"));targetBox.append(org);
      const toggle=()=>{personal.closest("label").hidden=mode.value==="organization";org.hidden=mode.value==="personal";};toggle();mode.addEventListener("change",toggle);
      targetBox.append(node("p","파트·팀·담당에 등록하는 자료는 해당 조직의 조회 권한이 있는 구성원에게 공유됩니다.","aik-muted"));
      selectedTarget=()=>({read_mode:queryMode.value,publication_target:{mode:mode.value,
        ...(mode.value!=="organization"?{personal_space_id:personal.value}:{}),
        ...(mode.value!=="personal"?{organization_space_id:grant.write_org_space_id}:{})}});
    };configure();connection.addEventListener("change",configure);
    form.append(button("자료 제출",async()=>{const run=await api(`/connections/${connection.value}/runs`,{operation_id:op(),kind:"manual.research",title:title.value,sources:[{id:"source-1",kind:"user_input",title:title.value,text:content.value}],result:result.value,...selectedTarget()});await detail(run.id);}));
  }
  const entry=document.getElementById("agentIntegrationsBtn");
  if(entry) {
    entry.hidden=true;
    // Static assets may be shared with an older running application process.
    // Expose the entry only when this origin has the authenticated API mounted.
    fetch(base+"/status",{credentials:"same-origin",cache:"no-store"}).then(r=>{entry.hidden=!r.ok;}).catch(()=>{});
  }
  entry?.addEventListener("click",async()=>{
    lastFocus=document.activeElement;show();body.append(node("p","연결 정보를 불러오는 중입니다…"));
    try {await connections();} catch(e) {if(body?.isConnected)body.replaceChildren(node("p",e.message));}
  });
})();

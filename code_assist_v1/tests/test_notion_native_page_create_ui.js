"use strict";
const assert = require("node:assert/strict"), fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname, "../notion_static/notion.js"), "utf8");
const start = source.indexOf('async function createPage(parentId = "", options = {}) {');
const end = source.indexOf("async function createSecondBrainTemplate()", start);
assert(start >= 0 && end > start);
const segment = source.slice(start, end), clone = value => JSON.parse(JSON.stringify(value));
function runtime() {
  const State = {user:{id:"owner"}, workspaceOwnerFilter:"", activePageId:"parent", collapsedPageIds:new Set(),
    workspaceAccess:{id:"workspace",teamspaces:[{id:"teamspace",role:"member"},{id:"team-b",role:"member"}]},
    navigation:{enabled:true, collaborativeSharing:true, collaborativePageCreation:true, collaborativeTeamPageCreation:true,
      pageCreationTemplates:["page","database","linked_database","task_tracker","meeting_notes","ai_notes"], workspaceId:"workspace",
      workspaces:[{id:"workspace", version:1, access:{role:"owner"}, teamspaces:[{id:"teamspace"}]}], pendingPageCreate:null}};
  const requests=[], opened=[], statuses=[];
  let operations=0;
  const context = {State, PageMetadataSync:null, selectedNavigationWorkspace:()=>State.navigation.workspaces.find(w=>w.id===State.navigation.workspaceId),
    navigationOperationId:()=>`create-request-${++operations}`, setStatus:(...args)=>statuses.push(args),
    flushPageDraft:async()=>true, saveCollapsedPages(){}, refreshOpenedPageAfterMutation:async()=>{}, refreshPageNavigation:async()=>{},
    loadPages:async()=>{throw Error("collaborative creation must use the draft-preserving navigation refresh");},
    PageFocusPolicy:{PAGE_TITLE:"title"}, openPage:async id=>{opened.push(id);State.activePageId=id;},
    api:async(url, options)=>{requests.push({url, body:clone(options.body)});return {page:{id:"created"}, workspace:{id:"workspace", version:2}, parent_link_saved:true};}};
  const teamPermission = source.slice(source.indexOf("function teamspaceCanCreatePage("), source.indexOf("function openTeamspaceCreateMenu("));
  vm.createContext(context); vm.runInContext(teamPermission + segment,context);
  return {context, State, requests, opened, statuses};
}
(async()=>{
  const unavailable=runtime(); unavailable.State.navigation.collaborativePageCreation=false;
  assert.equal(await unavailable.context.createPage(),null);assert.equal(unavailable.requests.length,0);

  const retry=runtime(), baseApi=retry.context.api;
  let failed=false;
  retry.context.api=async(...args)=>{const result=await baseApi(...args);if(!failed){failed=true;throw new Error("response lost");}return result;};
  assert.equal(await retry.context.createPage("",{title:"최초 제목"}),null);
  const result=await retry.context.createPage("",{title:"다시 클릭할 때의 다른 제목"});
  assert.equal(result.page.id,"created");assert.deepEqual(retry.requests[0],retry.requests[1]);
  assert.equal(retry.requests[1].body.title,"최초 제목");
  assert.equal(retry.State.navigation.workspaces[0].access.role,"owner");
  assert.equal(retry.State.navigation.workspaces[0].teamspaces[0].id,"teamspace");
  assert.equal(retry.State.navigation.pendingPageCreate,null);

  const display=runtime(), goodOpen=display.context.openPage;
  display.context.openPage=async()=>{throw new Error("open failed");};
  assert.equal(await display.context.createPage(),null);
  assert.equal(display.State.navigation.pendingPageCreate.result.page.id,"created");
  display.context.openPage=goodOpen; await display.context.createPage();
  assert.equal(display.requests.length,1,"retrying display after a committed create never creates another page");

  const concurrent=runtime(); let release;
  concurrent.context.api=async(url, options)=>{concurrent.requests.push({url, body:clone(options.body)});return new Promise(resolve=>{release=resolve;});};
  const flight=concurrent.context.createPage();
  assert.equal(await concurrent.context.createPage(),null);
  assert.equal(concurrent.requests.length,1);
  release({page:{id:"created"},workspace:{id:"workspace",version:2}});await flight;
  assert.deepEqual(concurrent.opened,["created"]);

  const switched=runtime(), switchedApi=switched.context.api;
  switched.context.api=async(...args)=>{const result=await switchedApi(...args);switched.State.navigation.workspaceId="elsewhere";return result;};
  assert.equal(await switched.context.createPage(),null);assert.deepEqual(switched.opened,[]);
  assert.equal(switched.State.navigation.workspaces[0].version,1);
  switched.State.navigation.workspaceId="workspace";
  await switched.context.createPage();
  assert.equal(switched.requests.length,1,"a late successful response retains its ID for the original workspace");

  const account=runtime(), accountApi=account.context.api;
  account.context.api=async(...args)=>{const result=await accountApi(...args);account.State.user.id="another-user";return result;};
  assert.equal(await account.context.createPage(),null);assert.deepEqual(account.opened,[]);
  account.context.api=accountApi;await account.context.createPage();
  assert.notEqual(account.requests[0].body.operation_id,account.requests[1].body.operation_id,
    "a different account must not reuse the previous actor's intent");

  const stale=runtime(), staleApi=stale.context.api;
  stale.context.api=async(...args)=>{const result=await staleApi(...args);stale.State.navigation.workspaces[0].version=9;return result;};
  await stale.context.createPage();assert.equal(stale.State.navigation.workspaces[0].version,9);

  const flush=runtime();flush.context.flushPageDraft=async()=>{flush.State.navigation.workspaceId="elsewhere";return true;};
  await flush.context.createPage("parent");assert.equal(flush.requests.length,0);

  const conflict=runtime(), conflictApi=conflict.context.api;let once=true;
  conflict.context.api=async(...args)=>{const result=await conflictApi(...args);if(once){once=false;throw Object.assign(new Error("CAS"),{status:409,data:{current_version:3}});}return result;};
  await conflict.context.createPage("",{title:"first"});await conflict.context.createPage("",{title:"second"});
  assert.equal(conflict.requests[1].body.base_version,3);
  assert.equal(conflict.requests[0].body.operation_id,conflict.requests[1].body.operation_id);
  assert.equal(conflict.requests[1].body.title,"first");

  const team=runtime(), teamApi=team.context.api; let teamFailed=false;
  team.State.navigation.workspaces[0].access.role="member";
  team.context.api=async(...args)=>{const result=await teamApi(...args);if(!teamFailed){teamFailed=true;throw Error("response lost");}return result;};
  assert.equal(await team.context.createPage("",{teamspaceId:"teamspace",title:"팀 문서"}),null);
  await team.context.createPage("",{teamspaceId:"teamspace",title:"변경된 제목"});
  assert.deepEqual(team.requests[0],team.requests[1]);
  assert.equal(team.requests[1].body.teamspace_id,"teamspace");
  assert.equal(team.requests[1].body.title,"팀 문서");
  assert.equal(team.requests[1].body.parent_id,"");
  await team.context.createPage("",{teamspaceId:"team-b"});
  assert.notEqual(team.requests[1].body.operation_id,team.requests[2].body.operation_id);
  assert.equal(team.requests[2].body.teamspace_id,"team-b");
  for (const alter of [h=>h.State.navigation.collaborativeTeamPageCreation=false,
      h=>h.State.navigation.enabled=false,h=>h.State.workspaceAccess.id="stale",
      h=>h.State.navigation.workspaces[0].access.role="restricted_member",
      h=>{h.State.navigation.workspaces[0].access.role="membership_admin";h.State.workspaceAccess.teamspaces[0].role="";},
      h=>h.State.workspaceOwnerFilter="other"]) {
    const denied=runtime();alter(denied);
    await denied.context.createPage("",{teamspaceId:"teamspace"});
    assert.equal(denied.requests.length,0,"team requests must never fall back to personal creation");
  }
  const mixed=runtime();await mixed.context.createPage("parent",{teamspaceId:"teamspace"});
  assert.equal(mixed.requests.length,0);
  const template=runtime(),templateApi=template.context.api;let lost=true;
  template.context.api=async(...args)=>{const result=await templateApi(...args);if(lost){lost=false;throw Error("lost");}return result;};
  await template.context.createPage("",{template:"database",teamspaceId:"teamspace",title:"원래 이름"});
  await template.context.createPage("",{template:"database",teamspaceId:"teamspace",title:"재시도 이름"});
  assert.deepEqual(template.requests[0],template.requests[1]);assert.equal(template.requests[1].body.template,"database");
  await template.context.createPage("",{template:"meeting_notes",teamspaceId:"teamspace"});
  assert.notEqual(template.requests[1].body.operation_id,template.requests[2].body.operation_id);
  const unsupported=runtime();unsupported.State.navigation.pageCreationTemplates=[];
  await unsupported.context.createPage("",{template:"database"});assert.equal(unsupported.requests.length,0);
  const linked=runtime(),linkedApi=linked.context.api;let linkedLost=true;
  linked.context.api=async(...args)=>{const result=await linkedApi(...args);if(linkedLost){linkedLost=false;throw Error("lost");}return result;};
  await linked.context.createPage("",{template:"linked_database",title:"원본 표",sourceDocumentId:"source-page",sourceDatabaseId:"source-db"});
  await linked.context.createPage("",{template:"linked_database",title:"다른 제목",sourceDocumentId:"source-page",sourceDatabaseId:"source-db"});
  assert.deepEqual(linked.requests[0],linked.requests[1]);
  assert.equal(linked.requests[1].body.source_document_id,"source-page");
  assert.equal(linked.requests[1].body.source_database_id,"source-db");
  const scopedLinked=runtime(),scopedApi=scopedLinked.context.api;let scopedLost=true;
  scopedLinked.context.api=async(...args)=>{const result=await scopedApi(...args);if(scopedLost){scopedLost=false;throw Error("lost");}return result;};
  await scopedLinked.context.createPage("",{template:"linked_database",title:"공유된 표",sourceDatabaseId:"source-db"});
  await scopedLinked.context.createPage("",{template:"linked_database",title:"다른 제목",sourceDatabaseId:"source-db"});
  assert.deepEqual(scopedLinked.requests[0],scopedLinked.requests[1]);
  assert.equal(scopedLinked.requests[1].body.source_database_id,"source-db");
  assert.equal(Object.hasOwn(scopedLinked.requests[1].body,"source_document_id"),false);
  const missingSource=runtime();await missingSource.context.createPage("",{template:"linked_database"});
  assert.equal(missingSource.requests.length,0);
  console.log("UI-NATIVE-CREATE: capability, frozen retries, display recovery, concurrent click, scope and version guards passed");
})().catch(error=>{console.error(error);process.exitCode=1;});

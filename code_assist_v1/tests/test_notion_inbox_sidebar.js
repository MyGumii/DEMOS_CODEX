"use strict";
const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync(require.resolve('../notion_static/notion.js'),'utf8');
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return{resolve,reject,promise};};
function fixture(){
  const nodes=new Map(),seen=new Set(),calls=[];let focused;
  const make=()=>({hidden:false,disabled:false,isConnected:true,value:'all',dataset:{},attrs:{},children:[],textContent:'',listeners:{},
    classList:{values:new Set(),toggle(name,force){if(force??!this.values.has(name))this.values.add(name);else this.values.delete(name);},contains(name){return this.values.has(name);}},setAttribute(k,v){this.attrs[k]=v;},addEventListener(k,v){this.listeners[k]=v;},focus(){focused=this;},
    set innerHTML(value){this.html=value;this.children.forEach(n=>n.isConnected=false);this.children=[...value.matchAll(/data-inbox-(item|filter)="([^"]+)"/g)].map(([,kind,id])=>Object.assign(make(),{dataset:{[kind==='item'?'inboxItem':'inboxFilter']:id}}));},
    get innerHTML(){return this.html||'';},replaceChildren(){this.innerHTML='';},querySelectorAll(){return this.children;},querySelector(){return this.children[0];}});
  const get=id=>{if(!nodes.has(id))nodes.set(id,make());return nodes.get(id);};
  const State={user:{id:'owner'},navigation:{workspaceId:'ws-a',collaborativeSharing:true,collaborativeSearch:false,workspaces:[]},
    sidebarMode:'inbox',workspaceOwnerFilter:'',pages:[{id:'page-a',title:'문서 A'}],comments:[],users:[],inboxItems:[],activePageId:'initial'};
  const context={State,$:get,window:{NotionUiInteraction:{place(){}}},document:{body:make(),get activeElement(){return focused||this.body;}},
    api:async(url,options)=>{calls.push({url,options});return{items:[]};},
    inboxSeenIds:()=>seen,markInboxItemsSeen:ids=>ids.forEach(id=>seen.add(id)),newOperationId:()=>`op-${calls.length}`,
    formatRelative:n=>String(n),escapeHtml:String,escapeAttr:String,
    openPage:async id=>{State.activePageId=id;},history:{replaceState(){}},pageUrl:id=>id,scrollToLocationBlock(){},closePageNavigationMenus(){}};
  vm.createContext(context);vm.runInContext(source.slice(source.indexOf('function renderInboxBadge()'),source.indexOf('let CommentLoadGeneration =')),context);
  const seed=items=>{State.inboxItems=items;vm.runInContext('InboxItemsScope=inboxScope()',context);context.renderInboxItems();};
  return{context,State,get,seen,calls,seed,focused:()=>focused};
}
const item=(id,extra={})=>({id,kind:'comment',collaborative:true,read:false,due:true,page_id:'page-a',page_title:'알림 '+id,created_at:Date.now()/1000,...extra});

test('UI-INBOX-ITEM-COMMENTS opens the exact canonical item before marking read',async()=>{
  const f=fixture(),opened=[];f.State.pageData={blocks:[{id:'block',props:{database_id:'db'}}]};
  f.context.openOwnedDatabaseItem=async(block,row)=>opened.push([block,row]);
  f.context.LinkedDatabaseItems={snapshot:()=>({rowId:'row',data:{source:{database_id:'db'}}})};f.context.ItemCommentsUi={view:{focus:()=>opened.push('focus')}};
  f.seed([item('row-alert',{item_target:{kind:'database_row',database_id:'db',row_id:'row'}})]);
  await f.get('#inboxList').children[0].listeners.click();
  assert.deepEqual(opened,[['block','row'],'focus']);assert.equal(f.calls.filter(c=>c.options?.method==='PATCH').length,1);
});

test('UI-INBOX-ITEM-COMMENTS a blocked or missing item never acknowledges its notification',async()=>{
  for(const missing of [false,true]){const f=fixture();f.State.pageData={blocks:missing?[]:[{id:'block',props:{database_id:'db'}}]};
    f.context.openOwnedDatabaseItem=async()=>false;f.context.LinkedDatabaseItems={snapshot:()=>({rowId:'other',data:{source:{database_id:'db'}}})};
    f.seed([item('row-alert',{item_target:{kind:'database_row',database_id:'db',row_id:'row'}})]);await f.get('#inboxList').children[0].listeners.click();assert.equal(f.calls.length,0);
  }
});

test('UI-INBOX-ITEM-COMMENTS opens a directly shared row and acknowledges only a matching scoped receipt',async()=>{
  const f=fixture(),opened=[];
  const notification={id:'direct-alert',kind:'comment_replied',workspace_id:'ws-a',
    database_id:'db-direct',database_title:'공유 표',row_id:'row-direct',
    item_target:{kind:'database_row',database_id:'db-direct',row_id:'row-direct'},
    workspace_epoch:2,document_epoch:3,database_epoch:4,notification_epoch:1,
    read:false,created_at:Date.now()/1000};
  f.context.api=async(url,options)=>{
    f.calls.push({url,options});
    if(options?.method==='PATCH')return{notification_id:'direct-alert',database_id:'db-direct',
      row_id:'row-direct',read:true};
    return{items:url.includes('/workspaces/')?[notification]:[]};
  };
  f.context.openIndependentDatabase=async(workspace,database)=>{
    opened.push([workspace,database]);f.State.independentDatabase={databaseId:database,scope:{}};
    return true;
  };
  f.context.openIndependentDatabaseItem=async(_scope,row)=>{opened.push(row);return true;};
  f.context.IndependentItemCommentsUi={view:{focus:()=>opened.push('focus')}};
  await f.context.loadInboxItems(true);
  assert.equal(f.State.inboxItems[0].page_id,null);
  assert.equal(f.State.inboxItems[0].page_title,'공유 표');
  await f.get('#inboxList').children[0].listeners.click();
  assert.deepEqual(opened,[['ws-a','db-direct'],'row-direct','focus']);
  assert.equal(f.State.inboxItems[0].read,true);
  const body=f.calls.find(call=>call.options?.method==='PATCH').options.body;
  assert.equal(body.expected_database_epoch,4);
  assert.equal(body.expected_notification_epoch,1);
  assert.equal('document_id' in body,false);
});

test('UI-INBOX-ITEM-COMMENTS keeps a direct notification unread on a mismatched receipt',async()=>{
  const f=fixture();
  f.seed([item('direct',{databaseOnly:true,page_id:null,workspace_id:'ws-a',
    database_id:'db-direct',row_id:'row-direct',workspace_epoch:2,
    document_epoch:3,database_epoch:4,notification_epoch:1})]);
  f.context.api=async()=>({notification_id:'direct',database_id:'wrong',row_id:'row-direct'});
  await f.context.markAllInboxItemsRead();
  assert.equal(f.State.inboxItems[0].read,false);
  assert.match(f.get('#inboxStatus').textContent,/다시 시도/);
});

test('UI-INBOX-SIDEBAR seconds and ISO timestamps group consistently, including invalid dates',()=>{
  const f=fixture(),now=new Date();now.setHours(12,0,0,0);const yesterday=new Date(now);yesterday.setDate(yesterday.getDate()-1);
  f.seed([item('today',{created_at:now.getTime()/1000}),item('yesterday',{created_at:yesterday.toISOString()}),item('unknown',{created_at:'invalid'})]);
  assert.match(f.get('#inboxList').innerHTML,/data-inbox-group="today"/);assert.match(f.get('#inboxList').innerHTML,/data-inbox-group="yesterday"/);
  assert.match(f.get('#inboxList').innerHTML,/data-inbox-group="earlier"/);
});

test('UI-INBOX-SIDEBAR server unread state cannot be hidden by stale browser seen IDs',()=>{
  const f=fixture();f.seen.add('server');f.seen.add('local');f.seed([item('server'),item('read',{read:true}),item('local',{collaborative:false})]);
  f.context.renderInboxBadge();assert.equal(f.get('#inboxBadge').textContent,'1');
  assert.equal(f.get('#workspaceName').classList.contains('has-unread'),true);
  f.get('#inboxFilter').value='due';f.context.renderInboxItems();assert.deepEqual(f.get('#inboxList').children.map(n=>n.dataset.inboxItem),['server']);
  f.State.inboxItems[0].read=true;f.context.renderInboxBadge();
  assert.equal(f.get('#workspaceName').classList.contains('has-unread'),false);
  f.State.inboxItems[0].read=false;f.State.navigation.workspaceId='ws-b';f.context.renderInboxBadge();
  assert.equal(f.get('#workspaceName').classList.contains('has-unread'),false);
});

test('UI-INBOX-SIDEBAR all supported filters distinguish server reads, local seen IDs and future reminders',()=>{
  const f=fixture();f.seen.add('server-unread');f.seen.add('legacy-seen');
  f.seed([item('server-unread'),item('server-read',{read:true}),item('mention',{kind:'mention'}),
    item('legacy-unread',{collaborative:false}),item('legacy-seen',{collaborative:false}),
    item('future',{kind:'reminder',collaborative:false,due:false}),
    item('due-reminder',{kind:'reminder',collaborative:false})]);
  const expected={all:['server-unread','server-read','mention','legacy-unread','legacy-seen','future','due-reminder'],
    due:['server-unread','mention','legacy-unread','due-reminder'],mention:['mention'],upcoming:['future']};
  for(const [value,ids] of Object.entries(expected)){
    f.get('#inboxFilter').value=value;f.context.renderInboxItems();
    assert.deepEqual(new Set(f.get('#inboxList').children.map(node=>node.dataset.inboxItem)),new Set(ids));
  }
  f.get('#inboxFilter').value='mention';f.seed([item('ordinary')]);
  assert.equal(f.get('#inboxList').children.length,0);assert.match(f.get('#inboxList').innerHTML,/표시할 알림이 없습니다/);
});

test('UI-INBOX-SIDEBAR filter actions return focus and reject a changed account, workspace or detached popup',()=>{
  const f=fixture();f.seed([item('mention',{kind:'mention'}),item('ordinary')]);f.get('#inboxFilterMenu').hidden=true;
  f.context.openInboxFilterMenu();
  f.get('#inboxFilterMenu').children.find(node=>node.dataset.inboxFilter==='mention').listeners.click();
  assert.equal(f.get('#inboxFilter').value,'mention');assert.equal(f.get('#inboxFilterMenu').hidden,true);
  assert.equal(f.get('#inboxFilterBtn').attrs['aria-expanded'],'false');assert.equal(f.focused(),f.get('#inboxFilterBtn'));
  assert.deepEqual(f.get('#inboxList').children.map(node=>node.dataset.inboxItem),['mention']);
  for(const change of ['account','workspace','owner-filter','sidebar','detached']){
    const stale=fixture();stale.seed([item('a')]);stale.get('#inboxFilterMenu').hidden=true;stale.context.openInboxFilterMenu();
    const button=stale.get('#inboxFilterMenu').children.find(node=>node.dataset.inboxFilter==='due');
    if(change==='account')stale.State.user.id='different-user';
    else if(change==='workspace')stale.State.navigation.workspaceId='different-workspace';
    else if(change==='owner-filter')stale.State.workspaceOwnerFilter='different-owner';
    else if(change==='sidebar')stale.State.sidebarMode='pages';
    else stale.context.closeInboxFilterMenu();
    button.listeners.click();assert.equal(stale.get('#inboxFilter').value,'all');assert.equal(stale.calls.length,0);
  }
});

test('UI-INBOX-SIDEBAR read-all applies only successful responses and leaves failures retryable',async()=>{
  const f=fixture();f.seed([item('ok'),item('failed'),item('local',{collaborative:false})]);
  f.context.api=async url=>{f.calls.push(url);if(url.endsWith('/failed'))throw Error('offline');return{};};
  await f.context.markAllInboxItemsRead();assert.equal(f.State.inboxItems[0].read,true);assert.equal(f.State.inboxItems[1].read,false);
  assert.ok(f.seen.has('local'));assert.equal(f.seen.has('failed'),false);assert.match(f.get('#inboxStatus').textContent,/다시 시도/);
  assert.equal(f.get('#inboxReadAllBtn').disabled,false);
  f.context.api=async()=>({});await f.context.markAllInboxItemsRead();assert.equal(f.State.inboxItems[1].read,true);assert.equal(f.get('#inboxReadAllBtn').disabled,true);
});

test('UI-INBOX-SIDEBAR pending read-all is single-flight and cannot change a switched workspace',async()=>{
  const f=fixture(),request=deferred();f.seed([item('old')]);f.context.api=(url)=>{f.calls.push(url);return request.promise;};
  const first=f.context.markAllInboxItemsRead();await f.context.markAllInboxItemsRead();assert.equal(f.calls.length,1);assert.match(f.calls[0],/workspaces\/ws-a\//);
  f.State.navigation.workspaceId='ws-b';f.State.inboxItems=[item('current')];request.resolve({});await first;
  assert.equal(f.State.inboxItems[0].read,false);assert.equal(f.seen.size,0);
});

test('UI-INBOX-SIDEBAR partial reads show failure and do not manufacture membership notifications',async()=>{
  const f=fixture();f.State.navigation.workspaces=[{id:'ws-a',owner_subject_id:'other',access:{role:'member'}}];
  f.context.api=async url=>{if(url==='/api/notion/inbox?limit=300')throw Error('offline');return{items:[]};};
  await f.context.loadInboxItems(true);assert.equal(f.State.inboxItems.length,0);assert.match(f.get('#inboxStatus').textContent,/일부 알림/);
  assert.equal(f.get('#inboxRefreshBtn').disabled,false);
});

test('UI-INBOX-SIDEBAR a blocked navigation or detached row cannot mark its notification read',async()=>{
  const f=fixture();f.seed([item('a')]);const old=f.get('#inboxList').children[0];
  f.context.openPage=async()=>false;await old.listeners.click();assert.equal(f.calls.length,0);assert.equal(old.disabled,false);
  f.context.renderInboxItems();await old.listeners.click();assert.equal(f.calls.length,0);
  f.State.sidebarMode='pages';await f.context.markAllInboxItemsRead();assert.equal(f.calls.length,0);
});

test('UI-INBOX-SIDEBAR opening a notification keeps the inbox selected and marks the actual item',async()=>{
  const f=fixture();f.seed([item('a')]);await f.get('#inboxList').children[0].listeners.click();
  assert.equal(f.State.sidebarMode,'inbox');assert.equal(f.State.activePageId,'page-a');assert.equal(f.State.inboxItems[0].read,true);
  assert.match(f.calls[0].url,/workspaces\/ws-a\/inbox\/a$/);
});

test('UI-INBOX-SIDEBAR a late refresh cannot overwrite a completed read acknowledgment',async()=>{
  const f=fixture(),read=deferred(),refresh=deferred();f.seed([item('a')]);
  f.context.api=(url,options)=>options?.method==='PATCH'?read.promise:url.includes('/workspaces/')?refresh.promise:Promise.resolve({items:[]});
  const marking=f.context.markAllInboxItemsRead(),loading=f.context.loadInboxItems(true);
  read.resolve({});await marking;
  refresh.resolve({items:[{id:'a',document_id:'page-a',kind:'comment_mentioned',read:false,created_at:Date.now()/1000}]});await loading;
  assert.equal(f.State.inboxItems[0].read,true);assert.equal(f.get('#inboxBadge').hidden,true);
});

test('UI-INBOX-SIDEBAR read-all restores retry or filter focus without stealing newer focus',async()=>{
  const f=fixture();f.seed([item('a')]);f.get('#inboxReadAllBtn').focus();
  f.context.api=async()=>{f.context.document.body.focus();throw Error('offline');};
  await f.context.markAllInboxItemsRead();assert.equal(f.focused(),f.get('#inboxReadAllBtn'));
  f.context.api=async()=>{f.context.document.body.focus();return{};};
  await f.context.markAllInboxItemsRead();assert.equal(f.focused(),f.get('#inboxFilterBtn'));
  f.seed([item('b')]);f.get('#inboxReadAllBtn').focus();
  f.context.api=async()=>{f.get('#pageTitle').focus();return{};};
  await f.context.markAllInboxItemsRead();assert.equal(f.focused(),f.get('#pageTitle'));
});

test('UI-INBOX-SIDEBAR refresh restores lost button focus only in the same visible inbox',async()=>{
  const f=fixture();f.get('#inboxRefreshBtn').focus();
  f.context.api=async()=>{f.context.document.body.focus();return{items:[]};};
  await f.context.loadInboxItems(true);assert.equal(f.focused(),f.get('#inboxRefreshBtn'));
  f.context.api=async()=>{f.context.document.body.focus();f.State.sidebarMode='pages';return{items:[]};};
  await f.context.loadInboxItems(true);assert.equal(f.focused(),f.context.document.body);
});

test('UI-INBOX-SIDEBAR background refresh preserves read failure until resolved, and clears it on scope change',async()=>{
  const f=fixture();f.seed([item('a')]);f.context.api=async()=>{throw Error('offline');};
  await f.context.markAllInboxItemsRead();
  f.context.api=async url=>({items:url.includes('/workspaces/')?[{id:'a',document_id:'page-a',read:false}]:[]});
  await f.context.loadInboxItems(false);assert.match(f.get('#inboxStatus').textContent,/다시 시도/);
  f.context.api=async url=>({items:url.includes('/workspaces/')?[{id:'a',document_id:'page-a',read:true}]:[]});
  await f.context.loadInboxItems(false);assert.equal(f.get('#inboxStatus').textContent,'');
  f.State.navigation.workspaceId='ws-b';
  f.context.api=async url=>({items:url.includes('/workspaces/')?[{id:'a',document_id:'page-a',read:false}]:[]});
  await f.context.loadInboxItems(false);assert.equal(f.get('#inboxStatus').textContent,'');
});

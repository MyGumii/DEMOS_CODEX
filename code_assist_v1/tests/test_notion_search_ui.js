'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require.resolve('../notion_static/notion.js'), 'utf8');
function extract(name) {
  const start = new RegExp(`^(?:async )?function ${name}\\(`, 'm').exec(source);
  if (!start) return '';
  const tail = source.slice(start.index);
  const next = /\n(?:async )?function \w+\(/.exec(tail);
  return tail.slice(0, next ? next.index : undefined);
}
const names = ['setWorkspaceNavigationEnabled', 'invalidateSearchPreview', 'invalidateGlobalSearch', 'globalSearchScopeKey', 'searchResultCanOpen',
  'searchItemUrl', 'openSearchItem', 'handleGlobalSearchKeydown', 'scheduleGlobalSearch', 'canonicalSearchDateAfter',
  'canonicalSearchSort', 'runGlobalSearch', 'openSearchOverlay', 'closeSearchOverlay'];
function harness() {
  const nodes = new Map(), timers = new Map(), requests = [], opened = [], rendered = [];
  let nextTimer = 0;
  const node = id => {
    if (!nodes.has(id)) nodes.set(id, {value: '', hidden: false, innerHTML: 'previous content', attrs: {},
      classList: {toggle() {}}, toggleAttribute() {},
      setAttribute(name, value) {this.attrs[name] = value;}, replaceChildren() {this.innerHTML = '';}, focus() {}});
    return nodes.get(id);
  };
  const context = vm.createContext({console, URLSearchParams, Date,
    State: {searchItems: [{id: 'old-page', title: 'Old result'}], activeSearchPageId: 'old-page',
      searchRequestSequence: 1, searchPending: false, searchTimer: null, searchTitleOnly: false,
      user: {id: 'owner'}, navigation: {authoritative: true, enabled: true, workspaceId: 'workspace-a',
        collaborativeSearch: true, sequence: 1}},
    $: node, SearchReturnFocus: null, document: {activeElement: null, body: {classList: {toggle() {}}}},
    clearTimeout: id => timers.delete(id), setTimeout: callback => {timers.set(++nextTimer, callback); return nextTimer;},
    renderSearchCreators() {}, renderSearchFilterChips() {}, closeSearchFilters() {}, setActiveNav() {}, syncSearchPreviewVisibility() {},
    setNavigationPending() {}, closeWorkspacePopover() {}, renderWorkspacePopover() {},
    escapeHtml: String, pageUrl: id => `/page/${id}`,
    independentDatabaseUrl: (workspace,id) => `/database/${workspace}/${id}`,
    PageFocusPolicy: {PRESERVE: 'preserve'},
    api: url => new Promise((resolve, reject) => requests.push({url, resolve, reject})),
    openPage: id => opened.push(id),
    openIndependentDatabase: (workspace,id) => opened.push(`database:${workspace}/${id}`),
    window: {open: url => opened.push(url)},
  });
  node('#searchScopeSelect').value = 'all'; node('#searchDateSelect').value = 'all';
  node('#searchSortSelect').value = 'relevance'; node('#globalSearchInput').value = 'initial';
  context.renderSearchResults = () => rendered.push(context.State.searchItems.map(item => item.id));
  context.renderSearchPreview = () => {node('#searchPreview').innerHTML = context.State.activeSearchPageId;};
  vm.runInContext(names.map(extract).join('\n'), context);
  return {context, node, timers, requests, opened, rendered};
}
function key(name, extra = {}) {return {key: name, preventDefault() {}, ...extra};}

(async () => {
  {
    const h=harness(); h.node('#searchPreview').innerHTML='Already displayed private body';
    h.context.setWorkspaceNavigationEnabled(false);
    assert.equal(h.node('#searchPreview').innerHTML,'');
    assert.equal(h.node('#searchResults').innerHTML,'');
    assert.equal(h.context.State.activeSearchPageId,'');
  }
  {
    const h = harness(), c = h.context;
    h.node('#globalSearchInput').value = 'new query';
    c.scheduleGlobalSearch();
    assert.equal(c.State.activeSearchPageId, '', 'new input must immediately invalidate the old Enter target');
    assert.equal(c.State.searchItems.length, 0);
    assert.equal(h.node('#searchPreview').innerHTML, '');
    assert.equal(h.node('#searchResults').attrs['aria-busy'], 'true');
    c.handleGlobalSearchKeydown(key('Enter'));
    c.handleGlobalSearchKeydown(key('Enter', {ctrlKey: true}));
    assert.deepEqual(h.opened, []);
  }
  {
    const h = harness(), c = h.context;
    const old = c.runGlobalSearch();
    h.node('#globalSearchInput').value = 'latest query';
    c.scheduleGlobalSearch();
    h.requests[0].resolve({items: [{id: 'stale-page'}]}); await old;
    assert.equal(c.State.searchItems.length, 0, 'an old response during debounce must not restore old results');
    const fresh = c.runGlobalSearch();
    h.requests[1].resolve({items: [{id: 'fresh-page'}]}); await fresh;
    assert.equal(c.State.activeSearchPageId, 'fresh-page');
    assert.equal(h.node('#searchResults').attrs['aria-busy'], 'false');
    c.handleGlobalSearchKeydown(key('Enter', {isComposing: true}));
    assert.deepEqual(h.opened, [], 'IME confirmation must not open a page');
    c.handleGlobalSearchKeydown(key('Enter'));
    assert.deepEqual(h.opened, ['fresh-page']);
  }
  {
    const h = harness(), c = h.context;
    const pending = c.runGlobalSearch();
    c.closeSearchOverlay();
    h.requests[0].resolve({items: [{id: 'closed-result'}]}); await pending;
    assert.equal(c.State.searchItems.length, 0);
    assert.equal(c.State.activeSearchPageId, '');
    assert.equal(h.timers.size, 0);
    c.openSearchOverlay();
    assert.equal(c.State.activeSearchPageId, '', 'reopening must not expose a previous keyboard target');
    c.closeSearchOverlay();
    assert.equal(h.timers.size, 0, 'closing also cancels the scheduled initial query');
  }
  {
    const h = harness(), c = h.context;
    const pending = c.runGlobalSearch();
    c.State.navigation.workspaceId = 'workspace-b';
    h.requests[0].resolve({items: [{id: 'wrong-workspace'}]}); await pending;
    assert.equal(c.State.searchItems.length, 0);
  }
  {
    const h = harness(), c = h.context;
    const pending = c.runGlobalSearch();
    h.requests[0].reject(new Error('network unavailable')); await pending;
    assert.equal(c.State.activeSearchPageId, '');
    assert.equal(c.State.searchPending, false);
    assert.match(h.node('#searchResults').innerHTML, /network unavailable/);
    c.handleGlobalSearchKeydown(key('Enter'));
    assert.deepEqual(h.opened, []);
  }
  {
    const h = harness(), c = h.context;
    c.State.navigation.authoritative = false;
    const pending = c.runGlobalSearch();
    assert.match(h.requests[0].url, /^\/api\/notion\/search\?/);
    c.State.user.id = 'different-subject';
    h.requests[0].resolve({items: [{id: 'previous-account-result'}]}); await pending;
    assert.equal(c.State.searchItems.length, 0, 'general search also rejects a prior account response');
    assert.equal(c.State.searchPending, false);
  }
  {
    const h = harness(), c = h.context;
    c.State.navigation.collaborativeSearch = false;
    c.fetchWorkspacePagesSnapshot = () => c.api('workspace-page-list');
    const old = c.runGlobalSearch();
    h.node('#searchCreatorSelect').value = 'me';
    const filtered = c.runGlobalSearch();
    h.requests[1].resolve([{id: 'mine', owner_user_id: 'owner'}, {id: 'other', owner_user_id: 'other'}]);
    await filtered;
    h.requests[0].resolve([{id: 'stale-unfiltered', owner_user_id: 'other'}]); await old;
    assert.deepEqual(Array.from(c.State.searchItems, item => item.id), ['mine']);
    assert.equal(c.State.activeSearchPageId, 'mine');
  }
  {
    const h=harness(), c=h.context;
    c.State.navigation.independentDatabaseSearch=true;
    const pending=c.runGlobalSearch();
    assert.equal(h.requests.length,2,'database-capable search requests both authorized result sets');
    assert.match(h.requests[1].url,/\/database-search\?/);
    h.requests[0].resolve({items:[]});
    h.requests[1].resolve({items:[{id:'database-e1',database_id:'database-e1',kind:'database',title:'Private Table'}]});
    await pending;
    assert.equal(c.State.activeSearchPageId,'database:database-e1');
    c.handleGlobalSearchKeydown(key('Enter'));
    assert.deepEqual(h.opened,['database:workspace-a/database-e1']);
  }
  console.log('UI-SEARCH: immediate invalidation, debounce race, close/reopen, workspace change, IME and failure checks passed');
})().catch(error => {console.error(error); process.exitCode = 1;});

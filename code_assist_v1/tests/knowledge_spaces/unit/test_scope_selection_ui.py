from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess

import pytest


pytestmark = pytest.mark.unit
_ROOT = Path(__file__).resolve().parents[3]
_MODULE = _ROOT / "static" / "knowledge_scope.js"
_INDEX = _ROOT / "static" / "index.html"
_GRAPH = _ROOT / "static" / "graph.js"


def _run_node(source: str) -> dict[str, object]:
    node = shutil.which("node")
    if node is None:
        pytest.fail("P3.6 browser-controller proof requires Node.js.")
    completed = subprocess.run(
        [node, "-e", source],
        cwd=_ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def test_preview_graph_discards_a_late_response_from_the_previous_space() -> None:
    result = _run_node(
        f"""
const nodes = new Map();
global.window = {{}};
global.$ = selector => {{
  if (!nodes.has(selector)) nodes.set(selector, {{hidden:false,innerHTML:'',value:'',textContent:''}});
  return nodes.get(selector);
}};
global.$$ = () => [];
global.api = async () => {{ throw new Error('legacy API must not be used'); }};
global.toast = () => {{}};
require({json.dumps(str(_GRAPH))});
const graph = window.SecondBrainGraph;
const drawn = [];
graph.draw = () => drawn.push(graph.data.counts.nodes);
let releasePersonal;
let releasePart;
const personal = new Promise(resolve => {{ releasePersonal = resolve; }});
const part = new Promise(resolve => {{ releasePart = resolve; }});
(async () => {{
  graph.useProvider(() => personal, {{readOnly:true,scopeLabel:'내 지식'}});
  const oldLoad = graph.load();
  graph.useProvider(() => part, {{readOnly:true,scopeLabel:'Part'}});
  const newLoad = graph.load();
  releasePart({{counts:{{nodes:1,edges:0}},nodes:[],edges:[]}});
  await newLoad;
  releasePersonal({{counts:{{nodes:8,edges:10}},nodes:[],edges:[]}});
  await oldLoad;
  console.log(JSON.stringify({{drawn,nodes:graph.data.counts.nodes,scope:graph.scopeLabel}}));
}})().catch(error => {{ console.error(error); process.exit(1); }});
"""
    )
    assert result == {"drawn": [1], "nodes": 1, "scope": "Part"}


@pytest.mark.scenario("CONV-003")
def test_p36_chat_selection_requires_new_conversation_and_preserves_badge() -> None:
    result = _run_node(
        f"""
const assert = require('node:assert/strict');
const ui = require({json.dumps(str(_MODULE))});
const personal = 'personal-space';
const part = 'part-space';
const oldId = 'old-conversation';
const newId = 'new-conversation';
const reads = [];
const creates = [];
const reloads = [];
const started = [];
function snapshot(selected, conversationId) {{
  const selectedId = selected || (conversationId === newId ? part : personal);
  const badgeSpace = conversationId === newId ? part : personal;
  return {{
    actor_user_id: 'member01', scope_epoch: 7, selected_space_id: selectedId,
    spaces: [
      {{id: personal, kind: 'personal', name: '내 지식', org_path: [], capabilities: ['read'], selected: selectedId === personal}},
      {{id: part, kind: 'part', name: 'Runtime Part', org_path: ['Root', 'Team', 'Runtime Part'], capabilities: ['read'], selected: selectedId === part}},
    ],
    conversation_badge: conversationId ? {{
      access_status: 'accessible', accessible: true, conversation_id: conversationId,
      conversation_row_version: 1, conversation_status: 'active', conversation_title: 'History',
      immutable: true, org_path: badgeSpace === part ? ['Root','Team','Runtime Part'] : [],
      space_id: badgeSpace, space_kind: badgeSpace === part ? 'part' : 'personal',
      space_name: badgeSpace === part ? 'Runtime Part' : '내 지식'
    }} : null,
  }};
}}
(async () => {{
    const controller = ui.createController({{
      readSnapshot: async (request) => {{ reads.push(request); return snapshot(request.selectedSpaceId, request.conversationId); }},
      createConversation: async (request) => {{ creates.push(request); return {{id: newId, space_id: request.spaceId}}; }},
      reloadManagementViews: async (request) => reloads.push(request),
      onConversationStarted: async (event) => started.push(event),
      operationIdFactory: () => 'operation-new-part',
  }});
  await controller.initialize({{mode: ui.CHAT_MODE, conversationId: oldId}});
  const oldBadge = controller.state().conversationBadge;
  await controller.selectSpace(part);
  assert.equal(controller.state().pendingSpaceId, part);
  assert.equal(controller.state().primaryActionLabel, '새 지식공간으로 새 대화 시작');
  assert.equal(controller.state().conversationBadge.space_id, personal);
  assert.equal(creates.length, 0);
  assert.equal(reloads.length, 0);
  const [firstStart, duplicateStart] = await Promise.all([
    controller.startNewConversation(),
    controller.startNewConversation(),
  ]);
  assert.equal(firstStart.currentConversationId, newId);
  assert.equal(duplicateStart.currentConversationId, newId);
  assert.deepEqual(creates, [{{operationId:'operation-new-part',spaceId: part}}]);
  assert.equal(controller.state().currentConversationId, newId);
  assert.equal(controller.state().conversationBadge.space_id, part);
  assert.equal(controller.state().conversationBadge.immutable, true);
  assert.equal(oldBadge.space_id, personal);
  assert.deepEqual(started, [{{oldConversationId: oldId, newConversationId: newId, spaceId: part}}]);
  console.log(JSON.stringify({{reads: reads.length, oldBadge: oldBadge.space_id, newBadge: controller.state().conversationBadge.space_id}}));
}})().catch((error) => {{ console.error(error); process.exit(1); }});
"""
    )
    assert result == {"reads": 3, "oldBadge": "personal-space", "newBadge": "part-space"}


def test_scope_selector_starts_the_new_space_conversation_without_pending_controls() -> None:
    result = _run_node(
        f"""
const assert = require('node:assert/strict');
const ui = require({json.dumps(str(_MODULE))});
const personal = 'personal-space';
const part = 'part-space';
const oldConversation = 'old-conversation';
const newConversation = 'new-conversation';
let creates = 0;
function snapshot(selectedSpaceId, conversationId) {{
  const selected = selectedSpaceId || (conversationId === newConversation ? part : personal);
  const badgeSpace = conversationId === newConversation ? part : personal;
  return {{
    actor_user_id: 'member01', scope_epoch: 1, selected_space_id: selected,
    spaces: [
      {{id:personal,kind:'personal',name:'내 지식',org_path:[],capabilities:['read'],selected:selected===personal}},
      {{id:part,kind:'part',name:'Part 01',org_path:['담당','팀','Part 01'],capabilities:['read'],selected:selected===part}},
    ],
    conversation_badge: conversationId ? {{
      access_status:'accessible',accessible:true,conversation_id:conversationId,
      conversation_row_version:1,conversation_status:'active',conversation_title:'Chat',immutable:true,
      org_path:badgeSpace===part?['담당','팀','Part 01']:[],space_id:badgeSpace,
      space_kind:badgeSpace===part?'part':'personal',space_name:badgeSpace===part?'Part 01':'내 지식',
    }} : null,
  }};
}}
const controller = ui.createController({{
  readSnapshot: async request => snapshot(request.selectedSpaceId, request.conversationId),
  createConversation: async request => {{ creates++; return {{id:newConversation,space_id:request.spaceId}}; }},
  reloadManagementViews: async () => {{ throw new Error('management reload must not run'); }},
  operationIdFactory: () => 'operation-part',
}});
const listeners = {{}};
const select = {{
  ownerDocument: {{createElement: () => ({{}})}}, disabled:false, value:personal, children:[],
  replaceChildren() {{ this.children=[]; }}, appendChild(value) {{ this.children.push(value); }},
  addEventListener(name, handler) {{ listeners[name]=handler; }},
}};
const root = {{hidden:true}};
(async () => {{
  await controller.initialize({{mode:ui.CHAT_MODE,conversationId:oldConversation}});
  ui.bindDom(controller, {{root,select}});
  select.value = part;
  await listeners.change();
  assert.equal(creates, 1);
  assert.equal(controller.state().currentConversationId, newConversation);
  assert.equal(controller.state().selectedSpaceId, part);
  assert.equal(controller.state().pendingSpaceId, null);
  assert.equal(root.hidden, false);
  console.log(JSON.stringify({{creates,current:controller.state().currentConversationId,selected:controller.state().selectedSpaceId}}));
}})().catch(error => {{ console.error(error); process.exit(1); }});
"""
    )
    assert result == {
        "creates": 1,
        "current": "new-conversation",
        "selected": "part-space",
    }


def test_web_scope_selector_disables_team_and_responsibility_only() -> None:
    result = _run_node(
        f"""
const assert = require('node:assert/strict');
const ui = require({json.dumps(str(_MODULE))});
const spaces = [
  {{id:'personal',kind:'personal',name:'Mine',org_path:[],capabilities:['read'],selected:true}},
  {{id:'part',kind:'part',name:'Part 01',org_path:['R','T','P'],capabilities:['read'],selected:false}},
  {{id:'team',kind:'team',name:'Team 01',org_path:['R','T'],capabilities:['read'],selected:false}},
  {{id:'responsibility',kind:'responsibility',name:'R',org_path:['R'],capabilities:['read'],selected:false}},
];
const controller = ui.createController({{
  readSnapshot: async request => ({{scope_epoch:1,selected_space_id:request.selectedSpaceId||'personal',spaces:spaces.map(space=>({{...space,selected:space.id===(request.selectedSpaceId||'personal')}})),conversation_badge:null}}),
  createConversation: async request => ({{id:'conversation',space_id:request.spaceId}}),
  reloadManagementViews: async () => {{}},
  operationIdFactory: () => 'operation',
}});
const listeners = {{}};
const select = {{ownerDocument:{{createElement:()=>({{}})}},disabled:false,value:'personal',children:[],replaceChildren(){{this.children=[];}},appendChild(value){{this.children.push(value);}},addEventListener(name,handler){{listeners[name]=handler;}}}};
(async()=>{{
  await controller.initialize({{mode:ui.MANAGEMENT_MODE}});
  ui.bindDom(controller,{{root:{{hidden:true}},select}});
  const disabled=select.children.filter(option=>option.disabled).map(option=>option.value);
  const enabled=select.children.filter(option=>!option.disabled).map(option=>option.value);
  assert.deepEqual(disabled,['team','responsibility']);
  assert.deepEqual(enabled,['personal','part']);
  select.value='team';
  await listeners.change();
  assert.equal(controller.state().selectedSpaceId,'personal');
  console.log(JSON.stringify({{disabled,enabled,selected:controller.state().selectedSpaceId}}));
}})().catch(error=>{{console.error(error);process.exit(1);}});
"""
    )
    assert result == {
        "disabled": ["team", "responsibility"],
        "enabled": ["personal", "part"],
        "selected": "personal",
    }


@pytest.mark.scenario("SPACE-004")
@pytest.mark.scenario("SPACE-005")
def test_p36_management_selection_reloads_every_dependent_surface() -> None:
    result = _run_node(
        f"""
const assert = require('node:assert/strict');
const ui = require({json.dumps(str(_MODULE))});
const reloads = [];
const snapshots = [];
const spaces = [
  {{id:'personal',kind:'personal',name:'내 지식',org_path:[],capabilities:['read']}},
  {{id:'part',kind:'part',name:'Part',org_path:['Root','Team','Part'],capabilities:['read']}},
];
(async () => {{
  const controller = ui.createController({{
    readSnapshot: async (request) => {{
      const selected = request.selectedSpaceId || 'personal';
      const value = {{actor_user_id:'member01',scope_epoch:1,selected_space_id:selected,spaces:spaces.map((space) => ({{...space,selected:space.id===selected}})),conversation_badge:null}};
      snapshots.push(value); return value;
    }},
    createConversation: async () => {{ throw new Error('chat mutation must not run'); }},
    reloadManagementViews: async (request) => {{
      assert.equal(controller.state().selectedSpaceId, request.spaceId);
      reloads.push(request);
    }},
    operationIdFactory: () => 'unused-operation',
  }});
  await controller.initialize({{mode:ui.MANAGEMENT_MODE}});
  await controller.selectSpace('part');
  assert.equal(controller.state().selectedSpaceId, 'part');
  assert.equal(controller.state().pendingSpaceId, null);
  assert.deepEqual(reloads, [{{spaceId:'part',surfaces:['list','search','graph','index_log','activity','candidates','pins']}}]);
  let rejected = false;
  try {{ await controller.selectSpace('responsibility-forged'); }} catch (error) {{ rejected = error.code === 'SPACE_NOT_SELECTABLE'; }}
  assert.equal(rejected, true);
  console.log(JSON.stringify({{reloads:reloads.length,snapshotCount:snapshots.length}}));
}})().catch((error) => {{ console.error(error); process.exit(1); }});
"""
    )
    assert result == {"reloads": 1, "snapshotCount": 2}


def test_hierarchy_scope_labels_keep_each_level_separate() -> None:
    result = _run_node(
        f"""
const assert = require('node:assert/strict');
const ui = require({json.dumps(str(_MODULE))});
assert.equal(ui.spaceDisplayLabel({{kind:'personal',name:'Private',org_path:[]}}), '내 지식 · Private');
assert.equal(ui.spaceDisplayLabel({{kind:'responsibility',name:'AMHS담당',org_path:['AMHS담당']}}), '담당 · AMHS담당');
assert.equal(ui.spaceDisplayLabel({{kind:'team',name:'Second Brain Preview Team',org_path:['AMHS담당','Second Brain Preview Team']}}), '팀 · Second Brain Preview Team');
assert.equal(
  ui.spaceDisplayLabel({{
    kind:'part',
    name:'Second Brain Preview Part',
    org_path:['AMHS담당','Second Brain Preview Team','Second Brain Preview Part'],
  }}),
  'Part · Second Brain Preview Part',
);
console.log(JSON.stringify({{label:'Part · Second Brain Preview Part'}}));
"""
    )
    assert result == {
        "label": "Part · Second Brain Preview Part"
    }


def test_hierarchy_scope_selector_orders_personal_part_team_responsibility() -> None:
    result = _run_node(
        f"""
const assert = require('node:assert/strict');
const ui = require({json.dumps(str(_MODULE))});
const ordered = ui.spacesForDisplay([
  {{id:'responsibility',kind:'responsibility',name:'AMHS담당'}},
  {{id:'team',kind:'team',name:'Second Brain Preview Team'}},
  {{id:'personal',kind:'personal',name:'내 지식'}},
  {{id:'part',kind:'part',name:'Second Brain Preview Part'}},
]);
assert.deepEqual(ordered.map(space => space.id), ['personal','part','team','responsibility']);
console.log(JSON.stringify({{order:ordered.map(space => space.kind)}}));
"""
    )
    assert result == {
        "order": ["personal", "part", "team", "responsibility"]
    }


def test_p36_static_component_has_only_the_compact_scope_selector() -> None:
    html = _INDEX.read_text(encoding="utf-8")
    source = _MODULE.read_text(encoding="utf-8")
    styles = (_INDEX.parent / "app.css").read_text(encoding="utf-8")
    assert 'id="knowledgeScopeControl" hidden' in html
    assert 'id="knowledgeScopeSelect"' in html
    assert 'id="conversationSpaceBadge"' not in html
    assert 'id="knowledgeScopeNewConversation"' not in html
    assert 'id="knowledgeScopeCancel"' not in html
    assert 'static/knowledge_scope.js?v=' in html
    assert html.index("static/knowledge_scope.js") < html.index("static/app.js")
    assert "fetch(" not in source
    assert "XMLHttpRequest" not in source
    assert "api/code/session" not in source
    assert "updateConversation" not in source
    assert "readSnapshot" in source
    assert "createConversation" in source
    assert ".knowledge-scope-control .scope-new-conversation" not in styles
    assert ".conversation-space-badge" not in styles


@pytest.mark.scenario("HSPACE-015", "HSPACE-016")
def test_scope_snapshot_preserves_exact_higher_native_authority_and_rejects_drift() -> None:
    result = _run_node(
        f"""
const assert = require('node:assert/strict');
const ui = require({json.dumps(str(_MODULE))});
const personal = '11111111-1111-4111-8111-111111111111';
const team = '22222222-2222-4222-8222-222222222222';
const assignment = '33333333-3333-4333-8333-333333333333';
const authority = {{
  capabilities: ['read','edit','publish','distribution_grant','distribution_revoke','aggregate_edge_correct'],
  native_space_id: team,
  publication_target: {{
    capability: 'publish', kind: 'team', role_assignment_generation: 4,
    role_assignment_id: assignment, space_id: team,
  }},
  role_assignment_generation: 4,
  role_assignment_id: assignment,
}};
function snapshot(nativeAuthority=authority) {{ return {{
  conversation_badge: null,
  scope_epoch: 8,
  selected_space_id: team,
  spaces: [
    {{id:personal,kind:'personal',name:'Mine',org_path:[],capabilities:['read'],native_authority:null,selected:false}},
    {{id:team,kind:'team',name:'Team A',org_path:['Responsibility','Team A'],capabilities:['read','edit'],native_authority:nativeAuthority,selected:true}},
  ],
}}; }}
const normalized = ui.normalizeSnapshot(snapshot());
const selected = normalized.spaces.find(space => space.id === team);
assert.equal(selected.native_authority.role_assignment_id, assignment);
assert.equal(selected.native_authority.publication_target.space_id, team);
assert.equal(Object.isFrozen(selected.native_authority), true);
assert.equal(Object.isFrozen(selected.native_authority.capabilities), true);
let code = null;
try {{
  ui.normalizeSnapshot(snapshot({{
    ...authority,
    publication_target: {{...authority.publication_target, space_id: personal}},
  }}));
}} catch (error) {{ code = error.code; }}
assert.equal(code, 'INVALID_SCOPE_SNAPSHOT');
console.log(JSON.stringify({{code, generation:selected.native_authority.role_assignment_generation}}));
"""
    )
    assert result == {"code": "INVALID_SCOPE_SNAPSHOT", "generation": 4}

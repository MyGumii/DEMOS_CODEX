from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess

import pytest


pytestmark = pytest.mark.unit
_ROOT = Path(__file__).resolve().parents[3]
_MODULE = _ROOT / "static" / "personal_wiki_ui.js"


def _run_node(source: str) -> dict[str, object]:
    node = shutil.which("node")
    if node is None:
        pytest.fail("Personal-Wiki UI proof requires Node.js.")
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


@pytest.mark.scenario("WIKI-018")
@pytest.mark.scenario("WIKI-020")
def test_personal_wiki_tree_is_ordered_lazy_and_page_bounded_for_one_thousand_wikis() -> None:
    result = _run_node(
        f"""
const assert=require('node:assert/strict');
const ui=require({json.dumps(str(_MODULE))});
const calls=[];
const ids=Array.from({{length:1000}},(_,index)=>`${{String(index+1).padStart(8,'0')}}-1111-4111-8111-111111111111`);
function profile(id,index){{return {{space_id:id,display_name:`Wiki ${{index}}`,is_default:index===0,lifecycle_status:'active',row_version:1}};}}
const transport={{
 listPersonalWikis:async request=>{{calls.push({{...request}});const start=request.cursor?Number(request.cursor):0;const source=request.lifecycleStatus==='active'?ids:[];return {{items:source.slice(start,start+request.limit).map((id,offset)=>profile(id,start+offset)),next_cursor:start+request.limit<source.length?String(start+request.limit):null}};}},
 searchPersonalWikis:async()=>({{items:[],next_cursor:null}}),
 createPersonalWiki:async()=>({{}}),renamePersonalWiki:async()=>({{}}),setDefaultPersonalWiki:async()=>({{}}),archivePersonalWiki:async()=>({{}}),restorePersonalWiki:async()=>({{}}),
}};
(async()=>{{
 assert.deepEqual(ui.GROUPS.map(value=>value.label),['내 지식','Part','팀','담당']);
 assert.equal(ui.PAGE_SIZE,50);
 const controller=ui.createController({{transport,selectSpace:async()=>{{}},openSearchResult:async()=>{{}},startFreshConversation:async()=>{{}},operationIdFactory:()=> 'operation',onChanged:async()=>{{}}}});
 await controller.initialize({{selectedSpaceId:ids[0],conversationBadge:null}});
 assert.equal(calls.length,1);
 assert.equal(calls[0].limit,50);
 assert.equal(controller.state().activeItems.length,50);
 assert.equal(controller.state().activeNextCursor,'50');
 await controller.loadMoreActive();
 assert.equal(calls.length,2);
 assert.equal(controller.state().activeItems.length,100);
 console.log(JSON.stringify({{calls:calls.length,domBound:controller.state().activeItems.length,order:ui.GROUPS.map(value=>value.kind)}}));
}})().catch(error=>{{console.error(error);process.exit(1);}});
"""
    )
    assert result == {
        "calls": 2,
        "domBound": 100,
        "order": ["personal", "part", "team", "responsibility"],
    }


@pytest.mark.scenario("WIKI-019")
@pytest.mark.scenario("WIKI-021")
def test_history_only_wiki_loads_archive_and_offers_restore_or_exact_fresh_session() -> None:
    result = _run_node(
        f"""
const assert=require('node:assert/strict');
const ui=require({json.dumps(str(_MODULE))});
const archived='11111111-1111-4111-8111-111111111111';
const active='22222222-2222-4222-8222-222222222222';
const calls=[];const fresh=[];
const activeProfile={{space_id:active,display_name:'Active',is_default:true,lifecycle_status:'active',row_version:3}};
const archivedProfile={{space_id:archived,display_name:'Archived',is_default:false,lifecycle_status:'archived',row_version:7}};
const transport={{
 listPersonalWikis:async request=>{{calls.push(['list',request.lifecycleStatus,request.limit]);return {{items:request.lifecycleStatus==='active'?[activeProfile]:[archivedProfile],next_cursor:null}};}},
 searchPersonalWikis:async()=>({{items:[],next_cursor:null}}),createPersonalWiki:async()=>({{}}),renamePersonalWiki:async()=>({{}}),setDefaultPersonalWiki:async()=>({{}}),archivePersonalWiki:async()=>({{}}),
 restorePersonalWiki:async request=>{{calls.push(['restore',request.spaceId,request.expectedRowVersion]);return {{}};}},
}};
(async()=>{{
 const controller=ui.createController({{transport,selectSpace:async()=>{{}},openSearchResult:async()=>{{}},startFreshConversation:async spaceId=>fresh.push(spaceId),operationIdFactory:()=> 'operation',onChanged:async()=>{{}}}});
 await controller.initialize({{selectedSpaceId:archived,conversationBadge:{{space_id:archived,history_only:true}}}});
 assert.equal(controller.state().archivedLoaded,true);
 assert.ok(controller.state().expanded.includes('archived'));
 await controller.restoreConversationWiki();
 await controller.startFresh();
 assert.deepEqual(calls.slice(0,2),[['list','active',50],['list','archived',50]]);
 assert.deepEqual(calls.find(value=>value[0]==='restore'),['restore',archived,7]);
 assert.deepEqual(fresh,[active]);
 console.log(JSON.stringify({{fresh:fresh[0],restoreVersion:calls.find(value=>value[0]==='restore')[2]}}));
}})().catch(error=>{{console.error(error);process.exit(1);}});
"""
    )
    assert result == {
        "fresh": "22222222-2222-4222-8222-222222222222",
        "restoreVersion": 7,
    }


def test_personal_wiki_dom_contract_is_accessible_and_has_real_history_actions() -> None:
    source = _MODULE.read_text(encoding="utf-8")
    assert 'host.setAttribute("role", "tree")' in source
    assert 'heading.setAttribute("aria-expanded"' in source
    assert 'children.setAttribute("role", "group")' in source
    assert 'button("이 Wiki 복구"' in source
    assert 'button("활성 Wiki에서 새 대화"' in source
    assert "fetch(" not in source


@pytest.mark.scenario("WIKI-018")
def test_personal_wiki_order_is_reorderable_persisted_and_applied_on_reload() -> None:
    result = _run_node(
        f"""
const assert=require('node:assert/strict');
const ui=require({json.dumps(str(_MODULE))});
const one='11111111-1111-4111-8111-111111111111';
const two='22222222-2222-4222-8222-222222222222';
const three='33333333-3333-4333-8333-333333333333';
let saved=[];
const profiles=[one,two,three].map((space_id,index)=>({{space_id,display_name:`Wiki ${{index+1}}`,is_default:index===0,lifecycle_status:'active',row_version:1}}));
const transport={{
 listPersonalWikis:async()=>({{items:profiles,next_cursor:null}}),searchPersonalWikis:async()=>({{items:[],next_cursor:null}}),
 createPersonalWiki:async()=>({{}}),renamePersonalWiki:async()=>({{}}),setDefaultPersonalWiki:async()=>({{}}),archivePersonalWiki:async()=>({{}}),restorePersonalWiki:async()=>({{}}),
}};
function controller(loadOrder){{return ui.createController({{
 transport,selectSpace:async()=>{{}},openSearchResult:async()=>{{}},startFreshConversation:async()=>{{}},operationIdFactory:()=> 'operation',
 loadOrder:()=>loadOrder,saveOrder:value=>{{saved=value.slice();}},
}});}}
(async()=>{{
 const first=controller([]);await first.initialize({{selectedSpaceId:one,conversationBadge:null}});
 first.reorder(three,one);
 assert.deepEqual(first.state().activeItems.map(value=>value.space_id),[three,one,two]);
 assert.deepEqual(saved,[three,one,two]);
 first.move(two,-1);
 assert.deepEqual(saved,[three,two,one]);
 const second=controller(saved);await second.initialize({{selectedSpaceId:three,conversationBadge:null}});
 assert.deepEqual(second.state().activeItems.map(value=>value.space_id),[three,two,one]);
 console.log(JSON.stringify({{order:saved}}));
}})().catch(error=>{{console.error(error);process.exit(1);}});
"""
    )
    assert result == {
        "order": [
            "33333333-3333-4333-8333-333333333333",
            "22222222-2222-4222-8222-222222222222",
            "11111111-1111-4111-8111-111111111111",
        ]
    }


def test_personal_wiki_navigation_renders_only_personal_directories_and_drag_controls() -> None:
    source = _MODULE.read_text(encoding="utf-8")
    assert 'GROUPS.filter((entry) => entry.kind === "personal")' in source
    assert 'handle.addEventListener("dragstart"' in source
    assert 'handle.addEventListener("keydown"' in source
    assert "controller.reorder(source, profile.space_id)" in source

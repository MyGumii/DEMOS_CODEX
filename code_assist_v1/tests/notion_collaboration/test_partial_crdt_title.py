"""UI-CRDT-TITLE: browser bridge seeds survive real durable transport/reopen."""
import base64
import json
from pathlib import Path
import shutil
import subprocess

from code_assist_v1.notion_collaboration import title_projection, search_projection
from code_assist_v1.tests.notion_collaboration.test_title_projection import _environment, append, title


SCRIPT = r"""
const fs=require('node:fs'),path=require('node:path');
const project=process.argv[1];require(path.join(project,'notion_static/vendor/yjs-13.6.32.bundle.js'));
const Y=globalThis.NotionYjs,Core=require(path.join(project,'notion_static/notion_editor_core.js'));
const Bridge=require(path.join(project,'notion_static/notion_collaboration_editor.js'));
const input=JSON.parse(fs.readFileSync(0,'utf8')),encode=bytes=>Buffer.from(bytes).toString('base64');
const state={page_id:input.page.document_id,title:input.page.title,blocks:input.page.blocks,can_edit:true,server_revision:input.page.revision};
if(input.mode==='body'){
  const d=new Y.Doc();d.getArray('block-order').insert(0,state.blocks.map(block=>block.id));
  for(const [index,block]of state.blocks.entries()){
    const m=new Y.Map(),text=new Y.Text();d.getMap('blocks').set(block.id,m);
    m.set('type',block.type);m.set('props',JSON.stringify(block.props));m.set('content',text);
    text.insert(0,index===0?'latest durable body':block.content);
  }
  process.stdout.write(JSON.stringify({body:encode(Y.encodeStateAsUpdate(d))}));d.destroy();
}else{
  const host=Core.createEditorHostAdapter({readDocument:()=>state,
    applyProjection:p=>{Object.assign(state,p);return {applied:true};}});
  const bridge=Bridge.createEditorBridge({Y,host}),events=[];
  bridge.subscribe(event=>events.push(event));bridge.configureDocument(state);
  bridge.applyFrames(input.frames.map(value=>({kind:'crdt.update',payload:new Uint8Array(Buffer.from(value,'base64'))})));
  bridge.finishReplay();
  const result={projection:bridge.projection(),seeds:events.filter(e=>e.seed).map(e=>encode(e.update))};
  if(input.clear){state.title='';host.notifyLocalMutation('document');
    const last=events.filter(e=>e.type==='local-update'&&!e.seed).at(-1);result.clear=encode(last.update);}
  process.stdout.write(JSON.stringify(result));bridge.dispose();host.dispose();
}
"""


def runtime(page, frames=(), *, mode="bootstrap", clear=False):
    project = Path(__file__).parents[2]
    result = subprocess.run([shutil.which("node"), "-e", SCRIPT, str(project)],
        input=json.dumps({"page": page, "mode": mode, "clear": clear,
            "frames": [base64.b64encode(value).decode() for value in frames]}).encode(),
        capture_output=True, check=True, timeout=15)
    return json.loads(result.stdout)


def frames(authority):
    with authority._reader() as con:
        return title_projection._frames(con, "page-e1", title_projection._generation(con, "page-e1"))


def test_ui_partial_title_real_transport_duplicate_seed_and_explicit_clear(tmp_path):
    authority, transport, _, _, workspace = _environment(tmp_path)
    canonical = authority.read_document("owner", "page-e1")
    body = runtime(canonical, mode="body")
    append(authority, transport, workspace, [base64.b64decode(body["body"])])
    assert title(authority, workspace)["page-e1"]["title"] == canonical["title"]
    left = runtime(canonical, frames(authority), clear=True)
    right = runtime(canonical, frames(authority))
    assert left["projection"]["title"] == right["projection"]["title"] == canonical["title"]
    assert left["projection"]["blocks"][0]["content"] == "latest durable body"
    assert len(left["seeds"]) == 1 and right["seeds"] == left["seeds"]
    append(authority, transport, workspace, [base64.b64decode(left["seeds"][0])])
    append(authority, transport, workspace, [base64.b64decode(right["seeds"][0])])
    assert title(authority, workspace)["page-e1"]["title"] == canonical["title"]
    reopened = runtime(canonical, frames(authority))
    assert reopened["projection"] == left["projection"] and not reopened["seeds"]
    append(authority, transport, workspace, [base64.b64decode(left["clear"])])
    assert title(authority, workspace)["page-e1"]["title"] == ""
    reopened = runtime(canonical, frames(authority))
    assert reopened["projection"]["title"] == "" and not reopened["seeds"]
    current = search_projection.materialize([frames(authority)])[0]
    assert current["title"] == "" and current["blocks"][0]["content"] == "latest durable body"
    after = authority.read_document("owner", "page-e1")
    assert (after["title"], after["blocks"], after["revision"]) == (canonical["title"], canonical["blocks"], canonical["revision"])

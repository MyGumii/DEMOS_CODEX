"""UI-R5: a linked view keeps its identity and local settings after retargeting."""

import json
from pathlib import Path
import shutil
import subprocess

from code_assist_v1.notion_collaboration import database_move


DELTA = Path(database_move.__file__).with_name("database_move_linked_delta.cjs")
VENDOR = Path(database_move.__file__).parents[1] / "notion_static/vendor/yjs-13.6.32.bundle.js"
INSPECT = r"""
require(process.argv[1]);const Y=globalThis.NotionYjs;
const input=JSON.parse(require('node:fs').readFileSync(0,'utf8')),doc=new Y.Doc();
for(const frame of input.frames)Y.applyUpdate(doc,Buffer.from(frame,'base64'));
Y.applyUpdate(doc,Buffer.from(input.update,'base64'));
const blocks=doc.getMap('blocks'),order=doc.getArray('block-order').toArray();
process.stdout.write(JSON.stringify(order.map(id=>({id,type:blocks.get(id).get('type'),
  content:blocks.get(id).get('content').toString(),
  props:JSON.parse(blocks.get(id).get('props'))}))));
"""


def _input(props, *, witness=None):
    linked = {"id": "linked-view", "type": "table", "content": "",
        "props": props}
    return {"contract": "code_assist.notion.database_move_linked_delta.v1",
        "documents": [{"document_id": "host", "generation": 1,
            "seed": {"title": "Host", "blocks": [
                {"id": "before", "type": "paragraph", "content": "Keep text", "props": {}},
                linked,
                {"id": "after", "type": "paragraph", "content": "More text", "props": {}}]},
            "frames": [], "references": [{"block_id": "linked-view",
                "expected_props": witness if witness is not None else props}],
            "source_document_id": "source", "destination_document_id": "destination",
            "database_id": "database-a"}]}


def _prepare(value):
    return subprocess.run([shutil.which("node"), str(DELTA)],
        input=json.dumps(value).encode(), capture_output=True, timeout=15)


def test_ui_r5_linked_delta_retargets_only_source_locator():
    props = {"linked_database": {"document_id": "source", "database_id": "database-a"},
        "database_id": "database-a", "views": [{"id": "view-1", "name": "Local board",
            "type": "table", "config": {"filters": [{"property": "name",
                "operator": "contains", "value": "A"}], "sorts": []}}],
        "active_view_id": "view-1", "indent": 2}
    value = _input(props)
    result = _prepare(value)
    assert result.returncode == 0, result.stderr.decode()
    prepared = json.loads(result.stdout)["documents"][0]
    assert prepared["retargeted"] == ["linked-view"]
    decoded = subprocess.run([shutil.which("node"), "-e", INSPECT, str(VENDOR)],
        input=json.dumps({"frames": [], "update": prepared["update"]}).encode(),
        capture_output=True, check=True, timeout=15)
    blocks = json.loads(decoded.stdout)
    assert [block["id"] for block in blocks] == ["before", "linked-view", "after"]
    assert [block["content"] for block in blocks] == ["Keep text", "", "More text"]
    assert blocks[1]["props"] == {**props, "linked_database": {
        "document_id": "destination", "database_id": "database-a"}}


def test_ui_r5_linked_delta_refuses_changed_view_witness():
    props = {"linked_database": {"document_id": "source", "database_id": "database-a"},
        "views": [{"id": "view-1", "name": "Original", "type": "table", "config": {}}],
        "active_view_id": "view-1"}
    changed = {**props, "views": [{"id": "view-1", "name": "Edited",
        "type": "table", "config": {}}]}
    assert _prepare(_input(props, witness=changed)).returncode != 0


def test_ui_r5_linked_delta_refuses_unwitnessed_second_reference():
    props = {"linked_database": {"document_id": "source", "database_id": "database-a"},
        "views": [{"id": "view-1", "name": "Original", "type": "table", "config": {}}],
        "active_view_id": "view-1"}
    value = _input(props)
    value["documents"][0]["seed"]["blocks"].append({"id": "hidden-link",
        "type": "table", "content": "", "props": props})
    assert _prepare(value).returncode != 0

"""UI-R5: prepare an owned database block transfer without touching linked views."""

import json
from pathlib import Path
import shutil
import subprocess

from code_assist_v1.notion_collaboration import page_move


DELTA = Path(page_move.__file__).with_name("page_move_delta.cjs")
INSPECT = r"""
const {transform}=require(process.argv[1]);
const Y=globalThis.NotionYjs;
const input=JSON.parse(require('node:fs').readFileSync(0,'utf8'));
const result=transform(input), doc=new Y.Doc();
Y.applyUpdate(doc,Buffer.from(result.update,'base64'));
const order=doc.getArray('block-order').toArray();
const blocks=doc.getMap('blocks');
process.stdout.write(JSON.stringify({removed:result.removed,placed:result.placed,
  inserted:result.inserted,changed:result.changed,
  blocks:order.map(id=>({id,type:blocks.get(id).get('type'),
    props:JSON.parse(blocks.get(id).get('props'))}))}));
"""


def _prepare(document_id, blocks, *, insert=None):
    input_value = {"document_id": document_id, "generation": 1,
        "seed": {"title": document_id, "blocks": blocks}, "frames": [],
        "child_id": "unused-page-child", "match_kind": "owned_database",
        "match_id": "database-a", "expected_match_count": 1 if insert is None else 0,
        "insert": insert}
    result = subprocess.run([shutil.which("node"), "-e", INSPECT, str(DELTA)],
        input=json.dumps(input_value).encode(), capture_output=True,
        check=True, timeout=15)
    return json.loads(result.stdout)


def test_ui_r5_owned_database_transfer_delta_keeps_identity_and_linked_views():
    owned = {"id": "owned-block", "type": "table", "content": "Owned",
        "props": {"database_id": "database-a"}}
    linked = {"id": "linked-block", "type": "table", "content": "Linked",
        "props": {"database_id": "database-a", "linked_database": {
            "document_id": "source", "database_id": "database-a"}}}
    other = {"id": "other-block", "type": "board", "content": "Other",
        "props": {"database_id": "database-b"}}
    source = _prepare("source", [owned, linked, other])
    assert source["removed"] == ["owned-block"]
    assert source["changed"] is True
    assert [block["id"] for block in source["blocks"]] == ["linked-block", "other-block"]

    destination = _prepare("destination", [{"id": "paragraph", "type": "paragraph",
        "content": "Keep", "props": {}}], insert=owned)
    assert destination["removed"] == []
    assert destination["inserted"] is True
    assert destination["placed"] == "owned-block"
    assert [block["id"] for block in destination["blocks"]] == ["paragraph", "owned-block"]


def test_ui_r5_python_preparation_binds_owned_database_identity():
    owned = {"id": "owned-block", "type": "table", "content": "Owned",
        "props": {"database_id": "database-a"}}
    sources = {
        "source": {"id": "source", "generation": 1, "frames": [],
            "snapshot": {"title": "Source", "blocks": [owned]},
            "match_kind": "owned_database", "match_id": "database-a",
            "expected_match_count": 1, "insert": None},
        "destination": {"id": "destination", "generation": 1, "frames": [],
            "snapshot": {"title": "Destination", "blocks": []},
            "match_kind": "owned_database", "match_id": "database-a",
            "expected_match_count": 0, "insert": owned},
    }
    deltas = page_move._prepare_deltas(sources, "unused-page-child", "r5-db-move")
    assert deltas["source"]["removed"] == ["owned-block"]
    assert deltas["source"]["placed"] is None
    assert deltas["destination"]["removed"] == []
    assert deltas["destination"]["placed"] == "owned-block"
    assert deltas["destination"]["inserted"] is True


def test_ui_r5_database_delta_rejects_missing_or_ambiguous_source():
    def source(blocks):
        return {"source": {"id": "source", "generation": 1, "frames": [],
            "snapshot": {"title": "Source", "blocks": blocks},
            "match_kind": "owned_database", "match_id": "database-a",
            "expected_match_count": 1, "insert": None}}

    from code_assist_v1.notion_collaboration.canonical_authority import AuthorityUnavailable
    import pytest
    with pytest.raises(AuthorityUnavailable):
        page_move._prepare_deltas(source([]), "unused", "missing-db")
    duplicate = [{"id": block, "type": "table", "content": "",
        "props": {"database_id": "database-a"}} for block in ("block-a", "block-b")]
    with pytest.raises(AuthorityUnavailable):
        page_move._prepare_deltas(source(duplicate), "unused", "duplicate-db")

"""UI-SYNC-DETACH-DELTA: real captured Yjs history stays read-only."""
from dataclasses import replace

import pytest

from code_assist_v1.notion_collaboration import synced_blocks as synced
from code_assist_v1.notion_collaboration import synced_detach_body as body
from code_assist_v1.notion_collaboration import synced_detach_delta as delta
from code_assist_v1.notion_collaboration import synced_detach_placement as placement
from code_assist_v1.notion_collaboration import synced_detach_remap as remap
from code_assist_v1.notion_collaboration import synced_detach_resources as resources
from code_assist_v1.notion_collaboration import search_projection
from code_assist_v1.tests.notion_collaboration.test_synced_detach_resources import (
    environment, member_environment, native_environment, archive_environment,
    guest_environment, image_environment, setup_resources, prepared, dump, text_update,
)


def test_ui_detach_delta_replays_real_destination_and_invalidates_after_source_change(environment):
    store, ws = environment; child, _, _ = setup_resources(store, ws)
    captured = resources.prepare_synced_resources(store.iam, prepared(store, ws, child))
    content = remap.prepare_detached_content(store.iam, captured)
    placed = placement.prepare_detached_placement(store.iam, content)
    prepared_body = body.prepare_detached_body(store.iam, placed)
    before = dump(store)
    result = delta.prepare_detached_delta(store.iam, prepared_body)
    assert dump(store) == before
    assert result.value()['commit_ready'] is False
    assert result.value()['required_integrations'] == ['atomic_destination_writer']
    assert result.value()['update_sha256'] == [delta._sha256(frame) for frame in result.update_frames]
    assert result.update_frames and all(0 < len(frame) <= delta.MAX_UPDATE_BYTES for frame in result.update_frames)
    destination = next(source for source in captured.detach.sources if source.document_id == child)
    replay = search_projection.materialize([list(destination.frames) + list(result.update_frames)])[0]
    assert replay['blocks'] == [{k:v for k,v in block.items() if k != 'position'}
                                for block in prepared_body.value()['blocks']]
    with store.iam._reader() as con:
        con.execute('BEGIN')
        delta.revalidate_detached_delta(store.iam, con, result)
        with pytest.raises(ValueError, match='modified'):
            delta.revalidate_detached_delta(store.iam, con, replace(result, update_frames=(b'bad',)))
    text_update(store, ws, 'page-e1', 'synced-original', 'delta 뒤 원본 변경')
    before = dump(store)
    with store.iam._reader() as con:
        con.execute('BEGIN')
        with pytest.raises(synced.SyncedSourceChanged):
            delta.revalidate_detached_delta(store.iam, con, result)
    assert dump(store) == before

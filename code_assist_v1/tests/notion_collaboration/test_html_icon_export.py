"""UI-ICON-EXPORT: current HTML view, exact icon scope and read-only boundaries."""
import base64
import hashlib
import io
import sqlite3

import pytest
from PIL import Image

from code_assist_v1.notion_collaboration import search_projection
from code_assist_v1.notion_collaboration.canonical_authority import NotFoundOrDenied
from code_assist_v1.notion_collaboration.page_appearance import update_page_appearance
from code_assist_v1.tests.notion_collaboration.test_visible_search import _environment, frames
from code_assist_v1.tests.notion_collaboration.test_title_projection import append, compact
from code_assist_v1.tests.notion_collaboration.test_search_authority import _grant
from code_assist_v1.tests.notion_collaboration.test_page_appearance import request


def dump(catalog):
    with sqlite3.connect(catalog.as_uri()+'?mode=ro',uri=True) as con:
        return tuple(con.iterdump())


def test_ui_icon_export_current_durable_content_compaction_and_empty_body(tmp_path, frames):
    a,transport,_,catalog,ws = _environment(tmp_path)
    update_page_appearance(a,'owner',ws,'page-e1',**request(a))
    grant = append(a,transport,ws,[frames['base'],frames['current']])
    before = dump(catalog)
    result = a.export_html('owner','page-e1')
    assert '<title>Visible projection</title>' in result['html']
    assert 'LatestBodyToken' in result['html'] and 'OldBodyToken' not in result['html']
    assert 'stroke="#337eb6"' in result['html']
    assert result['bundle'] == a.export_bundle('owner','page-e1')
    assert dump(catalog) == before
    compact(transport,grant,{'snapshot':frames['current'],'stateVector':frames['currentVector']})
    assert a.export_html('owner','page-e1')['html'] == result['html']
    append(a,transport,ws,[frames['empty']])
    assert '<section' not in a.export_html('owner','page-e1')['html']


def test_ui_icon_export_retries_edit_and_rechecks_permission_after_decode(tmp_path, frames, monkeypatch):
    a,t,_,_,ws = _environment(tmp_path)
    append(a,t,ws,[frames['base']])
    original = search_projection.materialize
    calls=[]
    def changed(docs):
        result=original(docs)
        if not calls:
            calls.append(True); append(a,t,ws,[frames['current']])
        return result
    monkeypatch.setattr(search_projection,'materialize',changed)
    assert 'LatestBodyToken' in a.export_html('owner','page-e1')['html']
    _grant(a,'page-e1','outsider','op-icon-export-grant-0001')
    def revoked(docs):
        result=original(docs)
        page=a.read_document('owner','page-e1')
        a.revoke_page_grant('owner','page-e1','subject','outsider',expected_workspace_epoch=page['workspace_epoch'],
            expected_document_epoch=page['document_epoch'],operation_id='op-icon-export-revoke-0001')
        return result
    monkeypatch.setattr(search_projection,'materialize',revoked)
    with pytest.raises(NotFoundOrDenied): a.export_html('outsider','page-e1')


def test_ui_icon_export_embeds_only_authorized_page_file_and_preserves_empty_icon(tmp_path):
    a,_,_,catalog,ws=_environment(tmp_path)
    stream=io.BytesIO(); Image.new('RGB',(2,2),'blue').save(stream,format='PNG'); raw=stream.getvalue()
    page=a.read_document('owner','page-e1')
    session=a.begin_upload('owner','page-e1',original_name='icon.png',mime_type='image/png',total_size=len(raw),
        expected_sha256='sha256:'+hashlib.sha256(raw).hexdigest(),chunk_size=65536,
        expected_document_sequence=page['document_sequence'],expected_workspace_epoch=page['workspace_epoch'],
        expected_document_epoch=page['document_epoch'],operation_id='op-icon-export-begin-0001')
    a.stage_chunk('owner',session['session_id'],chunk_index=0,stream=io.BytesIO(raw),operation_id='op-icon-export-chunk-0001')
    file_id=a.finalize_upload('owner',session['session_id'],operation_id='op-icon-export-final-0001')['file_id']
    update_page_appearance(a,'owner',ws,'page-e1',**request(a,'nf:'+file_id,'op-icon-export-file-0001'))
    before=dump(catalog)
    html=a.export_html('owner','page-e1')['html']
    assert 'data:image/png;base64,'+base64.b64encode(raw).decode() in html
    assert '/api/notion/' not in html and dump(catalog)==before
    with pytest.raises(NotFoundOrDenied): a.export_html('outsider','page-e1')
    # A corrupt foreign-page reference must not make an otherwise readable file exportable.
    a.create_document('owner',ws,'page-foreign-icon',title='Other',blocks=[],icon='nf:'+file_id,
        expected_workspace_epoch=page['workspace_epoch'],operation_id='op-icon-export-foreign-0001')
    assert 'data:image/png' not in a.export_html('owner','page-foreign-icon')['html']
    update_page_appearance(a,'owner',ws,'page-e1',**request(a,'','op-icon-export-empty-0001'))
    assert '<div class="page-icon">' not in a.export_html('owner','page-e1')['html']
    update_page_appearance(a,'owner',ws,'page-e1',**request(a,'👋','op-icon-export-emoji-0001'))
    html=a.export_html('owner','page-e1')['html']
    assert 'data:image/svg+xml;base64,' in html and 'CC BY 4.0' in html

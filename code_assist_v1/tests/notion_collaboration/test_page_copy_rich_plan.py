"""UI-PAGE-COPY: raw attributes obey the established reference remapper."""
from dataclasses import replace
import json

import pytest

from code_assist_v1.notion_collaboration import page_copy_remap, page_copy_rich, page_copy_source, database_trash
from code_assist_v1.notion_collaboration.canonical_authority import AuthorityUnavailable
from code_assist_v1.tests.notion_collaboration.test_page_copy_remap import fixture


def rich_fixture(link):
    source = fixture()
    root = source.documents[0]
    content = root.descriptor()['blocks'][0]['content']
    sidecar = {'title': [{'insert': root.descriptor()['title'], 'attributes': {'italic': True}}],
        'blocks': {'paragraph': [{'insert': content,
            'attributes': {'bold': False, 'link': link, 'custom': {'label': 'page-b'}}}]}}
    return replace(source, documents=(replace(root, rich_text_json=json.dumps(sidecar)), source.documents[1]))


@pytest.mark.parametrize('link,changed', [
    ('/page/page-b?label=page-a#block-target', True),
    ('https://app.example/page/page-b#block-target', True),
    ('https://external.example/page/page-b#block-target', False),
    ('/page/external-page#block-target', False),
    ('/api/notion/collaboration/files/file-source', True),
])
def test_ui_copy_rich_remaps_only_authorized_internal_links_and_leaves_values(link, changed):
    source = rich_fixture(link)
    root, child = page_copy_remap.remap_page_copy(source, 'rich-plan', local_origin='https://app.example').documents
    original = json.loads(source.documents[0].rich_text_json)
    copied = json.loads(root.rich_text_json)
    block_id = root.descriptor()['blocks'][0]['id']
    delta = copied['blocks'][block_id]
    assert copied['title'] == original['title']
    assert delta[0]['insert'] == original['blocks']['paragraph'][0]['insert']
    assert delta[0]['attributes']['bold'] is False
    assert delta[0]['attributes']['custom'] == {'label': 'page-b'}
    assert (delta[0]['attributes']['link'] != link) is changed
    if 'page-b' in link and changed:
        assert child.document_id in delta[0]['attributes']['link']
        assert child.descriptor()['blocks'][0]['id'] in delta[0]['attributes']['link']
    assert source == rich_fixture(link)


def test_ui_copy_plain_descriptor_has_no_new_initial_frame():
    plan = page_copy_remap.remap_page_copy(fixture(), 'plain-plan')
    assert page_copy_rich.prepare(plan, {document.document_id: document.descriptor() for document in plan.documents}) == {}


def test_ui_copy_rich_preparation_rejects_text_sidecar_mismatch():
    plan = page_copy_remap.remap_page_copy(rich_fixture('/page/page-b'), 'rich-plan-invalid')
    root = plan.documents[0]
    rich = json.loads(root.rich_text_json)
    rich['title'][0]['insert'] = 'changed title'
    invalid = replace(plan, documents=(replace(root, rich_text_json=json.dumps(rich)), plan.documents[1]))
    with pytest.raises(AuthorityUnavailable, match='differs from its descriptor'):
        page_copy_rich.prepare(invalid, {document.document_id: document.descriptor() for document in invalid.documents})


def test_ui_copy_rich_file_link_keeps_active_attachment_also_held_by_trash(monkeypatch):
    source = fixture().documents[0]
    descriptor = source.descriptor()
    descriptor.update(icon='', cover='', databases=[], blocks=[
        {'id': 'paragraph', 'type': 'paragraph', 'content': 'file link', 'props': {}}])
    rich = {'blocks': {'paragraph': [{'insert': 'file link',
        'attributes': {'link': '/api/notion/collaboration/files/file-source'}}]}}
    source = replace(source, descriptor_json=json.dumps(descriptor), rich_text_json=json.dumps(rich))
    monkeypatch.setattr(database_trash, 'trashed_file_ids', lambda con, document: {'file-source'})
    filtered = page_copy_source._exclude_archived_row_only_files(None, source)
    assert filtered.files_json == source.files_json and filtered.descriptor()['files'] == descriptor['files']
    plain = page_copy_source._exclude_archived_row_only_files(None, replace(source, rich_text_json=''))
    assert json.loads(plain.files_json) == [] and plain.descriptor()['files'] == []

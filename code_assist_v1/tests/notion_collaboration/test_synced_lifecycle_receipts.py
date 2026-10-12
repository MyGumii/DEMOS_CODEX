"""UI-SYNC-LIFECYCLE: exact receipts across whole DB/owning-page Trash.

Exercise actual HTTP commands and current authorization on isolated catalogs.
Lifecycle restore must preserve committed bodies and newer ordinary input;
recovery of the original operation must never repeat its effects.
"""
from copy import deepcopy

import pytest

from code_assist_v1.notion_collaboration import (
    database_trash, page_archive, synced_blocks as synced,
    synced_row_clipboard as paste, synced_detach_plan as detach_plan,
)
from code_assist_v1.notion_collaboration.canonical_authority import AuthorityUnavailable, NotFoundOrDenied
from code_assist_v1.tests.notion_collaboration.test_synced_database_text_fences import (
    sharing_environment, environment, body_environment, team_environment,
    file_environment, member_environment, native_environment, archive_environment,
    guest_environment, image_environment,
)
from code_assist_v1.tests.notion_collaboration.test_synced_row_clipboard import (
    seed as paste_seed, payload as paste_payload,
)
from code_assist_v1.tests.notion_collaboration.test_synced_database_detach import (
    seed as detach_seed, payload as detach_payload,
)
from code_assist_v1.tests.notion_collaboration.test_synced_database_body_read import (
    insert, dump, row_body, _schema,
)
from code_assist_v1.tests.notion_collaboration.test_synced_database_text_edit import snapshot
from code_assist_v1.tests.notion_collaboration.test_page_archive_http import client_for
from code_assist_v1.tests.notion_collaboration.test_page_copy_remap import block
from code_assist_v1.tests.notion_collaboration.test_database_features import _apply
from code_assist_v1.tests.notion_collaboration.test_synced_database_text_fences import (
    same_file, activity, db_trash, row_trash, placement, emoji, layout,
    default_template, sharing,
)


@pytest.fixture()
def receipt_environment(environment, request):
    store, workspace = environment
    # Parent-page lifecycle is admitted only through the exact template-default
    # marker. Sharing remains a separately staged marker; test its denial below
    # rather than expanding the product admission guard to make this test pass.
    migrations = [same_file, activity, db_trash, row_trash, placement, emoji, layout, default_template]
    if request.param == 'database':
        migrations.append(sharing)
    for migration in migrations:
        _apply(migration, store.db_path, store.user_data_root,
               f'lifecycle-before-{migration.TARGET_VERSION}.db')
    with store.iam._reader() as con:
        marker = con.execute('SELECT version FROM workspace_schema_migrations ORDER BY version DESC LIMIT 1').fetchone()
        assert marker['version'] == migrations[-1].TARGET_VERSION
        assert page_archive.marker_current(con) is (request.param == 'page')
    return store, workspace, request.param


def lifecycle(authority, workspace, document, database, boundary, archived, operation):
    with authority._reader() as con:
        owner = database_trash._owner if boundary == 'database' else page_archive._owner
        current_ws, current_page = owner(authority, con, 'owner', workspace, document)
        fields = {'archived': archived, 'operation_id': operation,
                  'expected_document_sequence': current_page['document_sequence'],
                  'expected_workspace_epoch': current_ws['security_epoch'],
                  'expected_document_epoch': current_page['access_epoch']}
        if boundary == 'database':
            fields['expected_database_version'] = con.execute(
                'SELECT row_version FROM workspace_collaboration_databases WHERE id=?',
                (database,)).fetchone()['row_version']
    if boundary == 'database':
        return database_trash.set_owned_database_trashed(authority, 'owner', workspace, document, database, **fields)
    return page_archive.set_page_archived(authority, 'owner', workspace, document, **fields)


def assert_no_store(response):
    assert response.headers['Cache-Control'] == 'no-store, max-age=0'


@pytest.mark.integration
@pytest.mark.regression
@pytest.mark.parametrize('effect', ['paste-row', 'detach-row', 'detach-template'])
@pytest.mark.parametrize('receipt_environment', ['database', 'page'], indirect=True)
@pytest.mark.parametrize('is_owner', [True, False])
def test_exact_committed_receipt_archive_authority_restore_and_later_input_preservation(
        receipt_environment, monkeypatch, effect, is_owner):
    store, workspace, boundary = receipt_environment
    actor = 'owner' if is_owner else 'schema-editor' if effect == 'detach-template' else 'content-editor'
    if effect == 'paste-row':
        paste_seed(store)
        value = paste_payload(store, workspace, operation='lifecycle-paste')
        url = f'/api/notion/workspaces/{workspace}/pages/page-e1/databases/database-e1/rows/row-e1/synced-paste'
    else:
        detach_seed(store, 'page', effect.removeprefix('detach-'))
        value = detach_payload(store, actor=actor, operation='lifecycle-detach')
        url = f'/api/notion/workspaces/{workspace}/pages/page-e1/synced-blocks/detach-peer/detach'
    # Use the existing real typed insert, so database Trash has a bound owned
    # table and does not need a synthetic direct SQL/layout repair.
    insert(store, 'lifecycle-db-block', block('lifecycle-db-block', 'table', database_id='database-e1'))
    if effect == 'paste-row':
        value = paste_payload(store, workspace, operation='lifecycle-paste')
    else:
        value = detach_payload(store, actor=actor, operation='lifecycle-detach')
    caps = store.capabilities
    monkeypatch.setattr(store, 'capabilities', lambda: {**caps(), 'collaborative_synced_detach': True})
    client, active_actor = client_for(monkeypatch, store)
    active_actor['id'] = actor
    saved = client.post(url, json=value)
    assert saved.status_code == 201, saved.json
    assert_no_store(saved)
    committed = deepcopy(saved.json)
    semantic = deepcopy(committed)
    if effect != 'paste-row':
        assert semantic.pop('status') == 'synced_detached'
    before_archive = deepcopy(snapshot(store))
    expected_database = deepcopy(store.iam.read_database('owner', 'database-e1'))
    result_url = url + '-result'

    # Result lookup/replay must not call preparation or recapture the source.
    def forbidden(*args, **kwargs):
        raise AssertionError('committed receipt must not recapture the source')
    monkeypatch.setattr(paste, 'prepare', forbidden)
    monkeypatch.setattr(detach_plan, 'prepare_synced_detach', forbidden)
    lifecycle(store.iam, workspace, 'page-e1', 'database-e1', boundary, True, 'lifecycle-archive')
    archived_dump = dump(store)
    with pytest.raises(NotFoundOrDenied):
        store.iam.read_database('owner', 'database-e1')
    archived_read = client.post(result_url, json=value)
    archived_replay = client.post(url, json=value)
    for response in (archived_read, archived_replay):
        assert_no_store(response)
    if effect == 'paste-row' or not is_owner:
        assert archived_read.status_code == archived_replay.status_code == 404
        assert '원본 내용' not in archived_read.get_data(as_text=True)
    else:
        assert archived_read.status_code == archived_replay.status_code == 200
        assert archived_read.json['state'] == 'committed'
        assert archived_read.json['current']['body_archived'] is True
        assert archived_read.json['current']['archived'] is (boundary == 'page')
        assert archived_read.json['result'] == {**semantic, 'replayed': True}
        assert archived_replay.json == {**committed, 'replayed': True}
    assert dump(store) == archived_dump

    lifecycle(store.iam, workspace, 'page-e1', 'database-e1', boundary, False, 'lifecycle-restore')
    restored = store.iam.read_database('owner', 'database-e1')
    assert restored['rows'] == expected_database['rows']
    assert restored['templates'] == expected_database['templates']
    assert snapshot(store)['blocks'] == before_archive['blocks']
    # A different ordinary write after restoration is deliberately newer than
    # the receipt. Recovery must leave this body and its version exactly intact.
    if effect == 'detach-template':
        template = next(t for t in restored['templates'] if t['id'] == 'detach-template')
        later = deepcopy(template['body'])
        next(b for b in later if b['id'] == 'detach-unrelated')['content'] = '복원 후 다른 창의 새 입력'
        _schema(store.iam, 'database.template.update', 'detach-template', {'body': later},
                'lifecycle-new-template-input', actor='owner', target_version=template['version'])
    else:
        row = next(r for r in restored['rows'] if r['id'] == 'row-e1')
        later = deepcopy(row['body']['blocks'])
        target = 'paste-keep' if effect == 'paste-row' else 'detach-unrelated'
        next(b for b in later if b['id'] == target)['content'] = '복원 후 다른 창의 새 입력'
        row_body(store, later, 'lifecycle-new-row-input')
    fresh_database = deepcopy(store.iam.read_database('owner', 'database-e1'))
    stable = dump(store)
    recovered = client.post(result_url, json=value)
    replayed = client.post(url, json=value)
    assert recovered.status_code == replayed.status_code == 200, (recovered.json, replayed.json)
    assert_no_store(recovered)
    assert_no_store(replayed)
    assert recovered.json['state'] == 'committed'
    assert recovered.json['result'] == {**semantic, 'replayed': True}
    assert replayed.json == {**committed, 'replayed': True}
    assert store.iam.read_database('owner', 'database-e1') == fresh_database
    assert dump(store) == stable
    active_actor['id'] = 'outsider'
    # A different actor cannot obtain the committed operation's result by ID.
    foreign = client.post(result_url, json=value)
    assert foreign.status_code == 404 or foreign.status_code == 200 and foreign.json == {'state': 'unknown'}
    assert dump(store) == stable


@pytest.mark.integration
@pytest.mark.regression
@pytest.mark.parametrize('archived', [True, False])
def test_page_lifecycle_unreviewed_sharing_marker_stays_closed_without_writes(
        sharing_environment, archived):
    store, workspace = sharing_environment
    with store.iam._reader() as con:
        page = store.iam._document(con, 'page-e1')
        current_ws = store.iam._workspace(con, workspace)
        assert not page_archive.marker_current(con)
    stable = dump(store)
    with pytest.raises(AuthorityUnavailable, match='canonical page archive is not installed'):
        page_archive.set_page_archived(store.iam, 'owner', workspace, 'page-e1', archived=archived,
            expected_document_sequence=page['document_sequence'],
            expected_workspace_epoch=current_ws['security_epoch'],
            expected_document_epoch=page['access_epoch'], operation_id='lifecycle-unsupported-marker')
    assert dump(store) == stable

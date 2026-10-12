"""Selected items keep their identities across owned database transfers."""

import json

import pytest

from code_assist_v1.notion_collaboration.canonical_authority import (
    InjectedFailure, NotFoundOrDenied, OperationConflict,
)
from code_assist_v1.notion_collaboration.database_row_move import (
    DatabaseRowMoveShapeUnavailable, move_owned_rows, read_owned_row_location,
)
from code_assist_v1.notion_collaboration.editing_authority import TargetVersionConflict
from code_assist_v1.notion_collaboration.database_row_trash import (
    list_trashed_database_rows, restore_database_rows_batch,
    trash_database_rows_batch,
)
from code_assist_v1.tests.notion_collaboration.test_database_row_icon_batch import (
    icon_batch_environment, appearance_environment, activity_environment,
    environment, body_environment, team_environment, file_environment,
    member_environment, native_environment, archive_environment,
    guest_environment, image_environment,
)
from code_assist_v1.tests.notion_collaboration.test_database_features import _surface
from code_assist_v1.tests.notion_collaboration.test_database_move_commit import _upload_png
from code_assist_v1.tests.notion_collaboration.test_database_trash_http import _client


def _destination(store, *, matching=True):
    with store.iam._transaction() as con:
        now = 1_800_000_001
        con.execute(
            "INSERT INTO workspace_collaboration_databases("
            "id,document_id,title,row_version,created_at,updated_at) "
            "VALUES('database-e2','page-e1','Destination',1,?,?)",
            (now, now),
        )
        con.execute(
            "INSERT INTO workspace_collaboration_database_properties("
            "id,database_id,name,property_type,position,config_json,row_version,"
            "created_at,updated_at) VALUES('property-e2','database-e2',?,?,0,'{}',1,?,?)",
            ('Status' if matching else 'Other', 'text', now, now),
        )


def _cross_destination(store, workspace_id):
    page = store.iam.read_document('owner', 'page-e1')
    store.iam.create_document('owner', workspace_id, 'page-e2',
        title='Another page', blocks=[{'id': 'other-body', 'type': 'paragraph',
            'content': 'Keep this page', 'props': {}}],
        expected_workspace_epoch=page['workspace_epoch'],
        operation_id='move-create-other-page')
    with store.iam._transaction() as con:
        now = 1_800_000_001
        con.execute(
            "INSERT INTO workspace_collaboration_databases("
            "id,document_id,title,row_version,created_at,updated_at) "
            "VALUES('database-e2','page-e2','Destination',1,?,?)",
            (now, now),
        )
        con.execute(
            "INSERT INTO workspace_collaboration_database_properties("
            "id,database_id,name,property_type,position,config_json,row_version,"
            "created_at,updated_at) VALUES('property-e2','database-e2',"
            "'Status','text',0,'{}',1,?,?)", (now, now),
        )
    page = store.iam.read_document('owner', 'page-e2')
    store.iam.execute_typed_command('owner', 'page-e2',
        command_kind='block.insert', target_id='owned-db-destination-block',
        payload={'type': 'table', 'content': 'Destination',
                 'props': {'database_id': 'database-e2'},
                 'after_block_id': 'other-body'},
        expected_document_sequence=page['document_sequence'],
        expected_workspace_epoch=page['workspace_epoch'],
        expected_document_epoch=page['document_epoch'],
        operation_id='move-create-other-database-block')


def _request(store, workspace_id, *, rows=None, operation='move-rows',
             destination_document='page-e1'):
    page = store.iam.read_document('owner', 'page-e1')
    destination = store.iam.read_document('owner', destination_document)
    source = store.iam.read_database('owner', 'database-e1')
    selected = source['rows'] if rows is None else rows
    return dict(
        actor_subject_id='owner', workspace_id=workspace_id,
        source_document_id='page-e1', source_database_id='database-e1',
        destination_document_id=destination_document,
        destination_database_id='database-e2',
        rows=[{'row_id': row['id'], 'expected_target_version': row['version']}
              for row in selected],
        expected_source_sequence=page['document_sequence'],
        expected_destination_sequence=destination['document_sequence'],
        expected_workspace_epoch=page['workspace_epoch'],
        expected_source_epoch=page['document_epoch'],
        expected_destination_epoch=destination['document_epoch'],
        operation_id=operation,
    )


@pytest.mark.integration
@pytest.mark.regression
def test_relation_cell_is_hidden_in_other_database_and_returns_with_row(
        icon_batch_environment):
    store, workspace_id = icon_batch_environment
    authority = store.iam
    _destination(store)
    with authority._transaction() as con:
        con.execute(
            "INSERT INTO workspace_collaboration_database_properties("
            "id,database_id,name,property_type,position,config_json,row_version,"
            "created_at,updated_at) VALUES("
            "'property-relation','database-e1','Related','relation',1,?,1,1,1)",
            (json.dumps({'target_database_id': 'database-e2'}),),
        )
        con.execute(
            "INSERT INTO workspace_collaboration_database_rows("
            "id,database_id,position,row_version,created_at,updated_at) "
            "VALUES('row-target','database-e2',0,1,1,1)",
        )
        con.execute(
            "INSERT INTO workspace_collaboration_database_cells("
            "row_id,property_id,value_json,row_version,updated_at) "
            "VALUES('row-e1','property-relation',?,1,1)",
            (json.dumps(['row-target']),),
        )
    before = authority.read_database('owner', 'database-e1')
    row = next(item for item in before['rows'] if item['id'] == 'row-e1')
    assert row['cells']['property-relation'] == ['row-target']

    move_owned_rows(authority, **_request(
        store, workspace_id, rows=[row], operation='relation-to-other-db'))
    destination = authority.read_database('owner', 'database-e2')
    moved = next(item for item in destination['rows'] if item['id'] == 'row-e1')
    assert 'property-relation' not in moved['cells']
    assert 'property-relation' not in {
        item['id'] for item in destination['properties']}

    page = authority.read_document('owner', 'page-e1')
    fences = dict(
        rows=[{'row_id': 'row-e1', 'expected_row_version': moved['version']}],
        operation_id='relation-row-archive',
        expected_document_sequence=page['document_sequence'],
        expected_workspace_epoch=page['workspace_epoch'],
        expected_document_epoch=page['document_epoch'],
        expected_database_version=destination['version'])
    archived = trash_database_rows_batch(
        authority, 'owner', workspace_id, 'page-e1', 'database-e2', **fences)
    trash_item = list_trashed_database_rows(
        authority, 'owner', workspace_id, 'page-e1', 'database-e2',
        row_id='row-e1')['items'][0]
    assert 'property-relation' not in trash_item['cells']
    restore_database_rows_batch(
        authority, 'owner', workspace_id, 'page-e1', 'database-e2',
        **{**fences,
           'rows': [{'row_id': 'row-e1',
                     'expected_row_version': archived['rows'][0]['row_version']}],
           'operation_id': 'relation-row-restore',
           'expected_document_sequence': archived['document_sequence']})

    page = authority.read_document('owner', 'page-e1')
    moved = next(item for item in authority.read_database(
        'owner', 'database-e2')['rows'] if item['id'] == 'row-e1')
    move_owned_rows(authority,
        actor_subject_id='owner', workspace_id=workspace_id,
        source_document_id='page-e1', source_database_id='database-e2',
        destination_document_id='page-e1', destination_database_id='database-e1',
        rows=[{'row_id': 'row-e1', 'expected_target_version': moved['version']}],
        expected_source_sequence=page['document_sequence'],
        expected_destination_sequence=page['document_sequence'],
        expected_workspace_epoch=page['workspace_epoch'],
        expected_source_epoch=page['document_epoch'],
        expected_destination_epoch=page['document_epoch'],
        operation_id='relation-back-to-original-db')
    restored = authority.read_database('owner', 'database-e1')
    original = next(item for item in restored['rows'] if item['id'] == 'row-e1')
    assert original['cells']['property-relation'] == ['row-target']
    with authority._reader() as con:
        assert not con.execute('PRAGMA foreign_key_check').fetchall()


@pytest.mark.integration
@pytest.mark.regression
def test_date_reminder_follows_cross_document_row_move_and_return(
        icon_batch_environment):
    store, workspace_id = icon_batch_environment
    authority = store.iam
    _cross_destination(store, workspace_id)
    with authority._transaction() as con:
        con.execute(
            "INSERT INTO workspace_collaboration_database_properties("
            "id,database_id,name,property_type,position,config_json,row_version,"
            "created_at,updated_at) VALUES("
            "'property-date','database-e1','Date','date',1,'{}',1,1,1)")
    reminder = {
        'at': '2027-01-14T00:00:00.000Z', 'lead': 0,
        'user_id': 'owner', 'preset': 'day_1', 'zone': 'Asia/Seoul'}
    value = {'start': '2027-01-15', 'reminder': reminder}
    page = authority.read_document('owner', 'page-e1')
    authority.execute_cell_batch(
        'owner', 'page-e1', database_id='database-e1', target_id='database-e1',
        payload={'cells': [{'row_id': 'row-e1', 'property_id': 'property-date',
                            'value': value, 'expected_target_version': 0}]},
        expected_document_sequence=page['document_sequence'],
        expected_workspace_epoch=page['workspace_epoch'],
        expected_document_epoch=page['document_epoch'],
        operation_id='date-reminder-before-row-move')
    before = authority.list_reminders('owner', workspace_id)['items']
    assert len(before) == 1
    assert before[0]['page_id'] == 'page-e1'

    source = authority.read_database('owner', 'database-e1')
    row = next(item for item in source['rows'] if item['id'] == 'row-e1')
    stored_date = row['cells']['property-date']
    request = _request(store, workspace_id, rows=[row],
                       operation='date-reminder-to-other-db',
                       destination_document='page-e2')
    moved_receipt = move_owned_rows(authority, **request)
    assert move_owned_rows(authority, **request) == moved_receipt
    target = authority.read_database('owner', 'database-e2')
    moved = next(item for item in target['rows'] if item['id'] == 'row-e1')
    target_date = next(item for item in target['properties']
                       if item['name'] == 'Date' and item['type'] == 'date')
    assert moved['cells'][target_date['id']] == stored_date
    after = authority.list_reminders('owner', workspace_id)['items']
    assert len(after) == 1
    assert after[0]['page_id'] == 'page-e2'
    assert after[0]['block_id'] == 'database-e2'
    assert after[0]['remind_at'] == reminder['at']

    source_page = authority.read_document('owner', 'page-e2')
    destination_page = authority.read_document('owner', 'page-e1')
    move_owned_rows(authority,
        actor_subject_id='owner', workspace_id=workspace_id,
        source_document_id='page-e2', source_database_id='database-e2',
        destination_document_id='page-e1', destination_database_id='database-e1',
        rows=[{'row_id': 'row-e1', 'expected_target_version': moved['version']}],
        expected_source_sequence=source_page['document_sequence'],
        expected_destination_sequence=destination_page['document_sequence'],
        expected_workspace_epoch=source_page['workspace_epoch'],
        expected_source_epoch=source_page['document_epoch'],
        expected_destination_epoch=destination_page['document_epoch'],
        operation_id='date-reminder-back-to-original-db')
    restored = next(item for item in authority.read_database(
        'owner', 'database-e1')['rows'] if item['id'] == 'row-e1')
    assert restored['cells']['property-date'] == stored_date
    returned = authority.list_reminders('owner', workspace_id)['items']
    assert len(returned) == 1
    assert returned[0]['page_id'] == 'page-e1'
    assert returned[0]['remind_at'] == reminder['at']


@pytest.mark.integration
@pytest.mark.regression
def test_same_document_row_move_keeps_id_cells_body_icon_and_one_receipt(
        icon_batch_environment):
    store, workspace_id = icon_batch_environment
    authority = store.iam
    _destination(store)
    _surface(authority, 'database.row.content.set', 'row-e1',
             {'blocks': [{'id': 'move-body', 'type': 'paragraph',
                          'content': 'keep this body', 'props': {}}]},
             'move-seed-body', actor='owner', target_version=1)
    _surface(authority, 'database.row.appearance.set', 'row-e1',
             {'icon': '🚀'}, 'move-seed-icon', actor='owner', target_version=2)
    page = authority.read_document('owner', 'page-e1')
    authority.add_comment('owner', 'page-e1', 'move-row-comment',
        content='keep this comment',
        anchor={'kind': 'database_row', 'database_id': 'database-e1',
                'row_id': 'row-e1'},
        expected_workspace_epoch=page['workspace_epoch'],
        expected_document_epoch=page['document_epoch'],
        operation_id='move-row-comment-operation')
    request = _request(store, workspace_id)
    before_source = authority.read_database('owner', 'database-e1')
    before_destination = authority.read_database('owner', 'database-e2')

    with pytest.raises(InjectedFailure):
        move_owned_rows(authority, **request, fault='after_domain')
    assert authority.read_database('owner', 'database-e1') == before_source
    assert authority.read_database('owner', 'database-e2') == before_destination
    stale = {**request, 'rows': [request['rows'][0],
        {**request['rows'][1], 'expected_target_version': 999}]}
    with pytest.raises(TargetVersionConflict):
        move_owned_rows(authority, **stale)
    assert authority.read_database('owner', 'database-e1') == before_source

    result = move_owned_rows(authority, **request)
    assert result['command_kind'] == 'database.rows.move'
    assert result['document_sequence'] == request['expected_source_sequence'] + 1
    assert [item['row_id'] for item in result['rows']] == ['row-e1', 'row-e2']
    assert move_owned_rows(authority, **request) == result
    with pytest.raises(OperationConflict):
        move_owned_rows(authority, **{**request, 'rows': request['rows'][:1]})
    assert authority.read_database('owner', 'database-e1')['rows'] == []
    moved = authority.read_database('owner', 'database-e2')['rows']
    assert [row['id'] for row in moved] == ['row-e1', 'row-e2']
    assert moved[0]['cells'] == {'property-e2': 'todo'}
    assert moved[0]['body']['blocks'][0]['content'] == 'keep this body'
    assert moved[0]['body']['blocks'][0]['id'] == 'move-body'
    assert moved[0]['appearance']['icon'] == '🚀'
    assert authority.list_comments('owner', 'page-e1', item_target={
        'kind': 'database_row', 'database_id': 'database-e2',
        'row_id': 'row-e1'})['items'][0]['id'] == 'move-row-comment'
    assert [row['version'] for row in moved] == [
        row['version'] + 1 for row in before_source['rows']]
    with authority._reader() as con:
        assert not con.execute('PRAGMA foreign_key_check').fetchall()
        for table in ('operations', 'events', 'audit_events', 'outbox'):
            assert con.execute(
                f'SELECT COUNT(*) FROM workspace_collaboration_{table} '
                'WHERE operation_id=?', (request['operation_id'],)
            ).fetchone()[0] == 1


@pytest.mark.integration
@pytest.mark.regression
def test_row_move_adds_missing_property_atomically_and_replays(
        icon_batch_environment):
    store, workspace_id = icon_batch_environment
    _destination(store, matching=False)
    request = _request(store, workspace_id)
    before = store.iam.read_database('owner', 'database-e1')
    empty_destination = store.iam.read_database('owner', 'database-e2')
    with pytest.raises(InjectedFailure):
        move_owned_rows(store.iam, **request, fault='after_domain')
    assert store.iam.read_database('owner', 'database-e1') == before
    assert store.iam.read_database('owner', 'database-e2') == empty_destination
    result = move_owned_rows(store.iam, **request)
    assert move_owned_rows(store.iam, **request) == result
    assert [(item['name'], item['type']) for item in result['created_properties']] == [
        ('Status', 'text')]
    created_id = result['created_properties'][0]['id']
    moved = store.iam.read_database('owner', 'database-e2')
    assert moved['rows'][0]['cells'][created_id] == 'todo'
    assert created_id not in moved['rows'][1]['cells']
    assert all('property-e2' not in row['cells'] for row in moved['rows'])


@pytest.mark.integration
@pytest.mark.regression
def test_row_move_requires_schema_edit_access_only_when_a_property_is_added(
        icon_batch_environment):
    store, workspace_id = icon_batch_environment
    _destination(store, matching=False)
    request = _request(store, workspace_id, operation='move-schema-role')
    before_source = store.iam.read_database('owner', 'database-e1')
    before_destination = store.iam.read_database('owner', 'database-e2')
    with pytest.raises(NotFoundOrDenied):
        move_owned_rows(store.iam, **{**request,
            'actor_subject_id': 'content-editor'})
    assert store.iam.read_database('owner', 'database-e1') == before_source
    assert store.iam.read_database('owner', 'database-e2') == before_destination
    result = move_owned_rows(store.iam, **{**request,
        'actor_subject_id': 'schema-editor',
        'operation_id': 'move-schema-role-authorized'})
    assert len(result['created_properties']) == 1
    page = store.iam.read_document('owner', 'page-e1')
    moved = store.iam.read_database('owner', 'database-e2')['rows'][0]
    returned = move_owned_rows(
        store.iam, 'content-editor', workspace_id, 'page-e1', 'database-e2',
        'page-e1', 'database-e1',
        rows=[{'row_id': moved['id'],
               'expected_target_version': moved['version']}],
        expected_source_sequence=page['document_sequence'],
        expected_destination_sequence=page['document_sequence'],
        expected_workspace_epoch=page['workspace_epoch'],
        expected_source_epoch=page['document_epoch'],
        expected_destination_epoch=page['document_epoch'],
        operation_id='move-schema-role-no-creation')
    assert returned['created_properties'] == []
    assert store.iam.read_database('owner', 'database-e1')['rows'][0][
        'cells']['property-e1'] == 'todo'


@pytest.mark.integration
@pytest.mark.regression
def test_row_move_keeps_date_when_same_name_has_wrong_type(
        icon_batch_environment):
    store, workspace_id = icon_batch_environment
    _destination(store)
    with store.iam._transaction() as con:
        now = 1_800_000_002
        con.execute(
            "INSERT INTO workspace_collaboration_database_properties("
            "id,database_id,name,property_type,position,config_json,row_version,"
            "created_at,updated_at) VALUES('property-date','database-e1',"
            "'Date','date',1,'{}',1,?,?)", (now, now))
        con.execute(
            "INSERT INTO workspace_collaboration_database_cells("
            "row_id,property_id,value_json,row_version,updated_at) "
            "VALUES('row-e1','property-date','\"2026-09-15\"',1,?)", (now,))
        con.execute(
            "INSERT INTO workspace_collaboration_database_properties("
            "id,database_id,name,property_type,position,config_json,row_version,"
            "created_at,updated_at) VALUES('property-other-date','database-e2',"
            "'Date','text',1,'{}',1,?,?)", (now, now))
    row = store.iam.read_database('owner', 'database-e1')['rows'][0]
    request = _request(store, workspace_id, rows=[row],
                       operation='move-date-wrong-type')
    result = move_owned_rows(store.iam, **request)
    assert [(item['name'], item['type']) for item in result['created_properties']] == [
        ('Date 1', 'date')]
    date_id = result['created_properties'][0]['id']
    moved = store.iam.read_database('owner', 'database-e2')['rows'][0]
    assert moved['cells']['property-e2'] == 'todo'
    assert moved['cells'][date_id] == '2026-09-15'
    assert 'property-other-date' not in moved['cells']
    def move_again(source_database, destination_database, operation):
        page = store.iam.read_document('owner', 'page-e1')
        current = next(item for item in store.iam.read_database(
            'owner', source_database)['rows'] if item['id'] == 'row-e1')
        return move_owned_rows(
            store.iam, 'owner', workspace_id, 'page-e1', source_database,
            'page-e1', destination_database,
            rows=[{'row_id': 'row-e1',
                   'expected_target_version': current['version']}],
            expected_source_sequence=page['document_sequence'],
            expected_destination_sequence=page['document_sequence'],
            expected_workspace_epoch=page['workspace_epoch'],
            expected_source_epoch=page['document_epoch'],
            expected_destination_epoch=page['document_epoch'],
            operation_id=operation,
        )
    returned = move_again('database-e2', 'database-e1',
                          'return-date-from-wrong-type')
    assert returned['created_properties'] == []
    original = store.iam.read_database('owner', 'database-e1')
    assert len(original['properties']) == 2
    assert next(item for item in original['rows'] if item['id'] == 'row-e1')[
        'cells']['property-date'] == '2026-09-15'
    repeated = move_again('database-e1', 'database-e2',
                          'repeat-date-to-wrong-type')
    assert repeated['created_properties'] == []
    destination = store.iam.read_database('owner', 'database-e2')
    assert len(destination['properties']) == 3
    assert destination['rows'][0]['cells'][date_id] == '2026-09-15'
    with store.iam._reader() as con:
        assert not con.execute('PRAGMA foreign_key_check').fetchall()


@pytest.mark.integration
@pytest.mark.regression
def test_row_move_http_is_default_off_and_replays_one_command(
        icon_batch_environment, monkeypatch):
    store, workspace_id = icon_batch_environment
    _destination(store)
    client, _actor = _client(store, monkeypatch, enabled=False)
    request = _request(store, workspace_id, operation='move-http')
    body = {
        'database_id': 'database-e1', 'target_id': 'database-e1',
        'command_kind': 'database.rows.move',
        'payload': {
            'destination_document_id': 'page-e1',
            'destination_database_id': 'database-e2',
            'expected_destination_sequence': request['expected_destination_sequence'],
            'expected_destination_epoch': request['expected_destination_epoch'],
            'rows': request['rows'],
        },
        'expected_document_sequence': request['expected_source_sequence'],
        'expected_workspace_epoch': request['expected_workspace_epoch'],
        'expected_document_epoch': request['expected_source_epoch'],
        'expected_target_version': None, 'operation_id': request['operation_id'],
    }
    path = f'/api/notion/workspaces/{workspace_id}/pages/page-e1/databases/commands'
    assert client.post(path, json=body).status_code == 503
    capabilities = store.capabilities
    monkeypatch.setattr(store, 'capabilities', lambda: {
        **capabilities(), 'collaborative_database_row_move_batch': True})
    response = client.post(path, json=body)
    assert response.status_code == 201, response.json
    assert response.json['command_kind'] == 'database.rows.move'
    assert client.post(path, json=body).json == response.json
    assert len(store.iam.read_database('owner', 'database-e1')['rows']) == 0
    assert len(store.iam.read_database('owner', 'database-e2')['rows']) == 2


@pytest.mark.integration
@pytest.mark.regression
def test_row_move_hides_inbound_relation_until_target_returns(
        icon_batch_environment):
    store, workspace_id = icon_batch_environment
    _destination(store)
    with store.iam._transaction() as con:
        now = 1_800_000_002
        con.execute("INSERT INTO workspace_collaboration_databases("
            "id,document_id,title,row_version,created_at,updated_at) "
            "VALUES('database-e3','page-e1','Related',1,?,?)", (now, now))
        con.execute("INSERT INTO workspace_collaboration_database_properties("
            "id,database_id,name,property_type,position,config_json,row_version,"
            "created_at,updated_at) VALUES('property-e3','database-e3','Link',"
            "'relation',0,?,1,?,?)",
            ('{"target_database_id":"database-e1"}', now, now))
        con.execute("INSERT INTO workspace_collaboration_database_rows("
            "id,database_id,position,row_version,created_at,updated_at) "
            "VALUES('row-e3','database-e3',0,1,?,?)", (now, now))
        con.execute("INSERT INTO workspace_collaboration_database_cells("
            "row_id,property_id,value_json,row_version,updated_at) "
            "VALUES('row-e3','property-e3',?,1,?)", ('["row-e1"]', now))
    authority = store.iam
    assert authority.read_database('owner', 'database-e3')['rows'][0][
        'cells']['property-e3'] == ['row-e1']
    request = _request(store, workspace_id, rows=[
        authority.read_database('owner', 'database-e1')['rows'][0]],
        operation='inbound-relation-to-other-db')
    with pytest.raises(InjectedFailure):
        move_owned_rows(authority, **request, fault='after_domain')
    assert authority.read_database('owner', 'database-e3')['rows'][0][
        'cells']['property-e3'] == ['row-e1']
    receipt = move_owned_rows(authority, **request)
    assert move_owned_rows(authority, **request) == receipt
    assert authority.read_database('owner', 'database-e3')['rows'][0][
        'cells']['property-e3'] == []
    with authority._reader() as con:
        assert con.execute(
            "SELECT value_json FROM workspace_collaboration_database_cells "
            "WHERE row_id='row-e3' AND property_id='property-e3'"
        ).fetchone()[0] == '["row-e1"]'
    moved = authority.read_database('owner', 'database-e2')['rows'][0]
    page = authority.read_document('owner', 'page-e1')
    move_owned_rows(authority, 'owner', workspace_id,
        'page-e1', 'database-e2', 'page-e1', 'database-e1',
        rows=[{'row_id': 'row-e1', 'expected_target_version': moved['version']}],
        expected_source_sequence=page['document_sequence'],
        expected_destination_sequence=page['document_sequence'],
        expected_workspace_epoch=page['workspace_epoch'],
        expected_source_epoch=page['document_epoch'],
        expected_destination_epoch=page['document_epoch'],
        operation_id='inbound-relation-back-to-original-db')
    assert authority.read_database('owner', 'database-e3')['rows'][0][
        'cells']['property-e3'] == ['row-e1']


@pytest.mark.integration
@pytest.mark.regression
def test_cross_document_row_move_commits_both_pages_and_retargets_comments(
        icon_batch_environment):
    store, workspace_id = icon_batch_environment
    authority = store.iam
    _cross_destination(store, workspace_id)
    _surface(authority, 'database.row.content.set', 'row-e1',
             {'blocks': [{'id': 'cross-body', 'type': 'paragraph',
                          'content': 'keep across pages', 'props': {}}]},
             'cross-seed-body', actor='owner', target_version=1)
    source = authority.read_document('owner', 'page-e1')
    authority.add_comment('owner', 'page-e1', 'cross-row-comment',
        content='keep cross-page comment',
        anchor={'kind': 'database_row', 'database_id': 'database-e1',
                'row_id': 'row-e1'},
        expected_workspace_epoch=source['workspace_epoch'],
        expected_document_epoch=source['document_epoch'],
        operation_id='cross-row-comment-operation')
    row = authority.read_database('owner', 'database-e1')['rows'][0]
    request = _request(store, workspace_id, rows=[row],
                       destination_document='page-e2', operation='cross-row-move')
    before_source = authority.read_database('owner', 'database-e1')
    before_destination = authority.read_database('owner', 'database-e2')
    with pytest.raises(InjectedFailure):
        move_owned_rows(authority, **request, fault='after_domain')
    assert authority.read_database('owner', 'database-e1') == before_source
    assert authority.read_database('owner', 'database-e2') == before_destination
    result = move_owned_rows(authority, **request)
    assert move_owned_rows(authority, **request) == result
    assert result['document_sequence'] == request['expected_source_sequence'] + 1
    assert result['destination_document_sequence'] == (
        request['expected_destination_sequence'] + 1)
    assert [item['id'] for item in authority.read_database('owner', 'database-e1')['rows']] == ['row-e2']
    moved = authority.read_database('owner', 'database-e2')['rows'][0]
    assert moved['id'] == 'row-e1'
    assert moved['cells'] == {'property-e2': 'todo'}
    assert moved['body']['blocks'][0]['content'] == 'keep across pages'
    assert authority.list_comments('owner', 'page-e2', item_target={
        'kind': 'database_row', 'database_id': 'database-e2',
        'row_id': 'row-e1'})['items'][0]['id'] == 'cross-row-comment'
    with authority._reader() as con:
        assert not con.execute('PRAGMA foreign_key_check').fetchall()
        assert con.execute("SELECT COUNT(*) FROM workspace_collaboration_events "
            "WHERE document_id='page-e2' AND event_type='database.surface.committed' "
            "AND document_sequence=?", (result['destination_document_sequence'],)
        ).fetchone()[0] == 1
        assert con.execute("SELECT COUNT(*) FROM workspace_collaboration_outbox "
            "WHERE document_id='page-e2' AND event_type='database.surface.committed' "
            "AND id=?", (result['destination_outbox_id'],)
        ).fetchone()[0] == 1
        destination_event = con.execute(
            'SELECT payload_json FROM workspace_collaboration_events WHERE id=?',
            (result['destination_event_id'],)
        ).fetchone()
        assert json.loads(destination_event['payload_json'])['destination_outbox_id'] == (
            result['destination_outbox_id'])


@pytest.mark.integration
@pytest.mark.regression
def test_cross_document_row_move_copies_item_attachment_and_retains_source_file(
        icon_batch_environment):
    store, workspace_id = icon_batch_environment
    authority = store.iam
    _cross_destination(store, workspace_id)
    file_id, png = _upload_png(store, 'row-move-file')
    _surface(authority, 'database.row.content.set', 'row-e1',
             {'blocks': [{'id': 'cross-file-body', 'type': 'file',
                          'content': 'image', 'props': {'file_id': file_id}}]},
             'cross-file-body-set', actor='owner', target_version=1)
    _surface(authority, 'database.row.appearance.set', 'row-e1',
             {'icon': 'nf:' + file_id}, 'cross-file-icon-set',
             actor='owner', target_version=2)
    request = _request(store, workspace_id, rows=[
        authority.read_database('owner', 'database-e1')['rows'][0]],
        destination_document='page-e2', operation='cross-file-move')
    result = move_owned_rows(authority, **request)
    assert result['copied_file_count'] == 1
    moved = authority.read_database('owner', 'database-e2')['rows'][0]
    copied_id = moved['body']['blocks'][0]['props']['file_id']
    assert copied_id != file_id
    assert moved['appearance']['icon'] == 'nf:' + copied_id
    assert authority.get_file('owner', copied_id)['path'].read_bytes() == png
    assert authority.get_file('owner', file_id)['path'].read_bytes() == png
    with authority._reader() as con:
        provenance = con.execute(
            'SELECT document_id,source_file_id,source_document_id,origin_kind '
            'FROM workspace_collaboration_files WHERE id=?', (copied_id,)
        ).fetchone()
        assert tuple(provenance) == ('page-e2', file_id, 'page-e1', 'copy')
        assert not con.execute('PRAGMA foreign_key_check').fetchall()


@pytest.mark.integration
@pytest.mark.regression
def test_moved_row_location_resolves_only_active_authorized_workspace_rows(
        icon_batch_environment, monkeypatch):
    store, workspace_id = icon_batch_environment
    authority = store.iam
    _cross_destination(store, workspace_id)
    location = read_owned_row_location(authority, 'owner', workspace_id, 'row-e1')
    assert (location['document_id'], location['database_id']) == ('page-e1', 'database-e1')
    request = _request(store, workspace_id, rows=[
        authority.read_database('owner', 'database-e1')['rows'][0]],
        destination_document='page-e2', operation='row-location-move')
    move_owned_rows(authority, **request)
    location = read_owned_row_location(authority, 'owner', workspace_id, 'row-e1')
    assert (location['document_id'], location['database_id'], location['row_id']) == (
        'page-e2', 'database-e2', 'row-e1')
    with pytest.raises(NotFoundOrDenied):
        read_owned_row_location(authority, 'owner', 'other-workspace', 'row-e1')
    with pytest.raises(NotFoundOrDenied):
        read_owned_row_location(authority, 'owner', workspace_id, 'unknown-row')
    store.identity_directory.replace(('owner', 'outsider'))
    with pytest.raises(NotFoundOrDenied):
        read_owned_row_location(authority, 'outsider', workspace_id, 'row-e1')
    client, actor = _client(store, monkeypatch, enabled=False)
    path = f'/api/notion/workspaces/{workspace_id}/database-rows/row-e1/location'
    response = client.get(path)
    assert response.status_code == 200
    assert response.json == location
    assert response.headers['Cache-Control'] == 'no-store, max-age=0'
    assert client.get(path.replace(workspace_id, 'other-workspace')).status_code == 404
    actor['id'] = 'outsider'
    assert client.get(path).status_code == 404

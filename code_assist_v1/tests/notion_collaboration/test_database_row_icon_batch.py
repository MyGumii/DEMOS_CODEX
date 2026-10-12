"""Selected-row icon changes are one authorized, replayable transaction."""

import pytest

from code_assist_v1 import notion_workspace_database_trash_migration as database_trash_migration
from code_assist_v1 import notion_workspace_database_row_trash_migration as row_trash_migration
from code_assist_v1.notion_collaboration.canonical_authority import (
    AuthorityUnavailable, InjectedFailure, OperationConflict,
)
from code_assist_v1.notion_collaboration.editing_authority import TargetVersionConflict
from code_assist_v1.notion_collaboration.iam_runtime import CollaborativeWorkspaceStore
from code_assist_v1.tests.notion_collaboration.test_database_activity_migration import activity_environment
from code_assist_v1.tests.notion_collaboration.test_database_features import _surface
from code_assist_v1.tests.notion_collaboration.test_database_row_content import _apply
from code_assist_v1.tests.notion_collaboration.test_database_trash_http import _client
from code_assist_v1.tests.notion_collaboration.test_owned_database_copy_commit import (
    environment, appearance_environment, body_environment, team_environment,
    file_environment, member_environment, native_environment, archive_environment,
    guest_environment, image_environment, owned_database_block,
)


@pytest.fixture()
def icon_batch_environment(activity_environment):
    source, workspace_id = activity_environment
    owned_database_block(source)
    _surface(source.iam, 'database.row.create', 'row-e2', {}, 'icon-batch-row-two')
    _apply(database_trash_migration, source.db_path, source.user_data_root,
        'before-icon-batch-db-trash.db')
    _apply(row_trash_migration, source.db_path, source.user_data_root,
        'before-icon-batch-row-trash.db')
    store = CollaborativeWorkspaceStore(source.db_path,
        user_data_root=source.user_data_root,
        identity_directory=source.identity_directory)
    return store, workspace_id


def _request(store, operation='icon-batch-one', icon='🚀'):
    page = store.iam.read_document('owner', 'page-e1')
    database = store.iam.read_database('owner', 'database-e1')
    return {'database_id': 'database-e1', 'target_id': 'database-e1',
        'payload': {'icon': icon, 'rows': [
            {'row_id': row['id'], 'expected_target_version': row['version']}
            for row in database['rows']]},
        'expected_document_sequence': page['document_sequence'],
        'expected_workspace_epoch': page['workspace_epoch'],
        'expected_document_epoch': page['document_epoch'],
        'expected_target_version': None, 'operation_id': operation}


@pytest.mark.integration
def test_selected_row_icon_batch_requires_2423(appearance_environment):
    store, _workspace_id = appearance_environment
    assert store.iam.read_database('owner', 'database-e1')['capabilities']['row_icon_batch'] is False
    with pytest.raises(AuthorityUnavailable):
        store.iam.execute_row_icon_batch('owner', 'page-e1', **_request(store))


@pytest.mark.integration
@pytest.mark.regression
def test_selected_row_icon_batch_rolls_back_replays_and_commits_once(
        icon_batch_environment):
    store, _workspace_id = icon_batch_environment
    authority = store.iam
    request = _request(store)
    before = authority.read_database('owner', 'database-e1')
    assert before['capabilities']['row_icon_batch'] is True
    with pytest.raises(InjectedFailure):
        authority.execute_row_icon_batch('owner', 'page-e1', **request,
            fault='after_domain')
    assert authority.read_database('owner', 'database-e1') == before
    stale = {**request, 'payload': {**request['payload'], 'rows': [
        request['payload']['rows'][0],
        {**request['payload']['rows'][1], 'expected_target_version': 99}]}}
    with pytest.raises(TargetVersionConflict):
        authority.execute_row_icon_batch('owner', 'page-e1', **stale)
    assert authority.read_database('owner', 'database-e1') == before
    result = authority.execute_row_icon_batch('owner', 'page-e1', **request)
    assert result['command_kind'] == 'database.rows.icon.set'
    assert len(result['rows']) == 2
    assert result['document_sequence'] == request['expected_document_sequence'] + 1
    assert authority.execute_row_icon_batch('owner', 'page-e1', **request) == result
    with pytest.raises(OperationConflict):
        authority.execute_row_icon_batch('owner', 'page-e1',
            **{**request, 'payload': {**request['payload'], 'icon': '⭐'}})
    changed = authority.read_database('owner', 'database-e1')['rows']
    assert [row['appearance']['icon'] for row in changed] == ['🚀', '🚀']
    assert [row['version'] for row in changed] == [
        row['version'] + 1 for row in before['rows']]


@pytest.mark.integration
@pytest.mark.regression
def test_selected_row_icon_batch_http_uses_one_command(icon_batch_environment,
        monkeypatch):
    store, workspace_id = icon_batch_environment
    client, _actor = _client(store, monkeypatch, enabled=False)
    request = _request(store, operation='icon-batch-http')
    path = f'/api/notion/workspaces/{workspace_id}/pages/page-e1/databases/commands'
    body = {**request, 'command_kind': 'database.rows.icon.set'}
    assert client.post(path, json=body).status_code == 503
    capabilities = store.capabilities
    monkeypatch.setattr(store, 'capabilities', lambda: {
        **capabilities(), 'collaborative_database_row_icon_batch': True})
    response = client.post(path, json=body)
    assert response.status_code == 201, response.json
    assert response.json['rows'][0]['appearance']['icon'] == '🚀'
    assert response.json['rows'][1]['appearance']['icon'] == '🚀'

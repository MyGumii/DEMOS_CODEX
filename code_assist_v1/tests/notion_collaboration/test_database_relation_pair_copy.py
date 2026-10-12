"""UI-R5: copying one database must not inherit its external inverse pair."""

import json

import pytest

from code_assist_v1.notion_collaboration.database_copy import copy_owned_database
from code_assist_v1.notion_collaboration.database_relation_pair import enable_relation_pair
from code_assist_v1.notion_collaboration.canonical_authority import NotFoundOrDenied
from code_assist_v1.notion_collaboration.page_settings import update_page_settings
from code_assist_v1.tests.notion_collaboration.test_database_row_move_batch import _cross_destination
from code_assist_v1.tests.notion_collaboration.test_database_row_page_move import (
    placement_environment, icon_batch_environment, activity_environment,
    environment, appearance_environment, body_environment, team_environment,
    file_environment, member_environment, native_environment, archive_environment,
    guest_environment, image_environment,
)
from code_assist_v1.tests.notion_collaboration.test_file_copy_provenance import (
    upload, request as file_request, seed_copy_file,
)


@pytest.mark.integration
@pytest.mark.regression
def test_ui_r5_template_relation_defaults_create_and_apply_inverse_atomically(
        placement_environment):
    store, workspace_id = placement_environment
    _cross_destination(store, workspace_id)
    authority = store.iam
    with authority._transaction() as con:
        con.execute(
            "INSERT INTO workspace_collaboration_database_properties("
            "id,database_id,name,property_type,position,config_json,"
            "row_version,created_at,updated_at) VALUES("
            "'template-relation','database-e1','Related','relation',3,?,1,1,1)",
            (json.dumps({'target_database_id': 'database-e2'}),))
        con.execute(
            "INSERT INTO workspace_collaboration_database_rows("
            "id,database_id,position,row_version,created_at,updated_at) "
            "VALUES('template-target-row','database-e2',0,1,1,1)")
    source = authority.read_document('owner', 'page-e1')
    target = authority.read_document('owner', 'page-e2')
    enable_relation_pair(
        authority, actor_subject_id='owner', workspace_id=workspace_id,
        source_document_id='page-e1', source_database_id='database-e1',
        source_property_id='template-relation', target_document_id='page-e2',
        target_database_id='database-e2', inverse_property_id='template-back',
        inverse_name='Back', expected_source_sequence=source['document_sequence'],
        expected_target_sequence=target['document_sequence'],
        expected_workspace_epoch=source['workspace_epoch'],
        expected_source_epoch=source['document_epoch'],
        expected_target_epoch=target['document_epoch'],
        expected_source_property_version=1, operation_id='template-relation-pair-enable')

    def command(kind, target_id, payload, operation_id, *, version=0, schema=False):
        page = authority.read_document('owner', 'page-e1')
        method = (authority.execute_schema_command if schema
                  else authority.execute_surface_command)
        return method(
            'owner', 'page-e1', database_id='database-e1',
            command_kind=kind, target_id=target_id, payload=payload,
            expected_document_sequence=page['document_sequence'],
            expected_workspace_epoch=page['workspace_epoch'],
            expected_document_epoch=page['document_epoch'],
            expected_target_version=version, operation_id=operation_id)

    command('database.template.create', 'template-with-relation',
            {'name': 'Linked', 'body': [],
             'default_values': {'template-relation': ['template-target-row']}},
            'template-with-relation-create', schema=True)
    source_before_create = authority.read_document('owner', 'page-e1')
    target_before = authority.read_document('owner', 'page-e2')['document_sequence']
    created = command('database.row.create', 'template-created-row',
                      {'template_id': 'template-with-relation'},
                      'template-relation-row-create')
    assert created['applied_defaults'] == {'template-relation': ['template-target-row']}
    assert len(created['reciprocal_documents']) == 1
    assert authority.read_document('owner', 'page-e2')['document_sequence'] == target_before + 1
    assert authority.read_database('owner', 'database-e2')['rows'][0][
        'cells']['template-back'] == ['template-created-row']
    replay = authority.execute_surface_command(
        'owner', 'page-e1', database_id='database-e1',
        command_kind='database.row.create', target_id='template-created-row',
        payload={'template_id': 'template-with-relation'},
        expected_document_sequence=source_before_create['document_sequence'],
        expected_workspace_epoch=source_before_create['workspace_epoch'],
        expected_document_epoch=source_before_create['document_epoch'],
        expected_target_version=0, operation_id='template-relation-row-create')
    assert replay == created
    assert authority.read_document('owner', 'page-e2')['document_sequence'] == target_before + 1
    assert next(row for row in authority.read_database('owner', 'database-e1')['rows']
                if row['id'] == 'template-created-row')['cells'][
                    'template-relation'] == ['template-target-row']

    existing = next(row for row in authority.read_database('owner', 'database-e1')['rows']
                    if row['id'] == 'row-e1')
    applied = command('database.row.template.apply', 'row-e1',
                      {'template_id': 'template-with-relation'},
                      'template-relation-row-apply', version=existing['version'])
    assert applied['applied_defaults'] == {'template-relation': ['template-target-row']}
    assert authority.read_database('owner', 'database-e2')['rows'][0][
        'cells']['template-back'] == ['template-created-row', 'row-e1']

    command('database.template.create', 'template-invalid-relation',
            {'name': 'Invalid', 'body': [],
             'default_values': {'template-relation': ['missing-row']}},
            'template-invalid-relation-create', schema=True)
    source_before = authority.read_document('owner', 'page-e1')['document_sequence']
    target_before = authority.read_document('owner', 'page-e2')['document_sequence']
    with pytest.raises(NotFoundOrDenied):
        command('database.row.create', 'template-invalid-row',
                {'template_id': 'template-invalid-relation'},
                'template-invalid-relation-row')
    assert authority.read_document('owner', 'page-e1')['document_sequence'] == source_before
    assert authority.read_document('owner', 'page-e2')['document_sequence'] == target_before
    assert not any(row['id'] == 'template-invalid-row' for row in
                   authority.read_database('owner', 'database-e1')['rows'])

    target_page = authority.read_document('owner', 'page-e2')
    update_page_settings(
        authority, 'owner', workspace_id, 'page-e2', updates={'locked': True},
        expected_document_sequence=target_page['document_sequence'],
        expected_workspace_epoch=target_page['workspace_epoch'],
        expected_document_epoch=target_page['document_epoch'],
        operation_id='template-target-lock')
    source_before = authority.read_document('owner', 'page-e1')['document_sequence']
    with pytest.raises(PermissionError, match='locked'):
        command('database.row.create', 'template-locked-target-row',
                {'template_id': 'template-with-relation'},
                'template-locked-target-row-create')
    assert authority.read_document('owner', 'page-e1')['document_sequence'] == source_before
    assert not any(row['id'] == 'template-locked-target-row' for row in
                   authority.read_database('owner', 'database-e1')['rows'])


@pytest.mark.integration
@pytest.mark.regression
def test_ui_r5_template_uploaded_file_defaults_are_scoped_and_replayed(
        placement_environment):
    store, _workspace_id = placement_environment
    authority = store.iam
    file_id = upload(store, operation='template-file-upload')['file_id']

    def command(kind, target_id, payload, operation_id, *, version=0, schema=False):
        page = authority.read_document('owner', 'page-e1')
        method = authority.execute_schema_command if schema else authority.execute_surface_command
        return method('owner', 'page-e1', database_id='database-e1',
                      command_kind=kind, target_id=target_id, payload=payload,
                      expected_document_sequence=page['document_sequence'],
                      expected_workspace_epoch=page['workspace_epoch'],
                      expected_document_epoch=page['document_epoch'],
                      expected_target_version=version, operation_id=operation_id)

    command('database.property.create', 'template-files',
            {'name': 'Attachments', 'type': 'files', 'config': {}},
            'template-files-property', schema=True)
    created = command('database.template.create', 'template-with-file',
                      {'name': 'Files', 'body': [], 'default_values': {
                          'template-files': [{'file_id': file_id, 'name': 'spoofed'}]}},
                      'template-file-create', schema=True)
    assert created['target_version'] == 1
    with authority._transaction() as con:
        uploaded = con.execute(
            'SELECT original_name,mime_type,size_bytes FROM workspace_collaboration_files '
            'WHERE id=?', (file_id,)).fetchone()
    expected = [{'file_id': file_id, 'name': uploaded['original_name'],
                 'mime_type': uploaded['mime_type'], 'size': uploaded['size_bytes']}]
    assert authority.read_database('owner', 'database-e1')['templates'][0][
        'default_values']['template-files'] == expected

    page_before = authority.read_document('owner', 'page-e1')
    created_row = command('database.row.create', 'template-file-row',
                          {'template_id': 'template-with-file'}, 'template-file-row-create')
    assert created_row['applied_defaults']['template-files'] == expected
    assert authority.execute_surface_command(
        'owner', 'page-e1', database_id='database-e1',
        command_kind='database.row.create', target_id='template-file-row',
        payload={'template_id': 'template-with-file'},
        expected_document_sequence=page_before['document_sequence'],
        expected_workspace_epoch=page_before['workspace_epoch'],
        expected_document_epoch=page_before['document_epoch'],
        expected_target_version=0, operation_id='template-file-row-create') == created_row
    existing = next(row for row in authority.read_database('owner', 'database-e1')['rows']
                    if row['id'] == 'row-e1')
    applied = command('database.row.template.apply', 'row-e1',
                      {'template_id': 'template-with-file'}, 'template-file-apply',
                      version=existing['version'])
    assert applied['applied_defaults']['template-files'] == expected

    before = authority.read_document('owner', 'page-e1')['document_sequence']
    with pytest.raises(NotFoundOrDenied):
        command('database.template.create', 'template-foreign-file',
                {'name': 'Foreign', 'body': [], 'default_values': {
                    'template-files': [{'file_id': 'missing-file'}]}},
                'template-foreign-file-create', schema=True)
    assert authority.read_document('owner', 'page-e1')['document_sequence'] == before
    assert not any(item['id'] == 'template-foreign-file' for item in
                   authority.read_database('owner', 'database-e1')['templates'])

    stale_file_id = upload(store, operation='template-file-stale-upload')['file_id']
    command('database.template.create', 'template-stale-file',
            {'name': 'Stale', 'body': [], 'default_values': {
                'template-files': [{'file_id': stale_file_id}]}},
            'template-stale-file-create', schema=True)
    authority.delete_file('owner', stale_file_id,
                          operation_id='template-stale-file-delete', **file_request(store))
    before = authority.read_document('owner', 'page-e1')['document_sequence']
    with pytest.raises(NotFoundOrDenied):
        command('database.row.create', 'template-stale-file-row',
                {'template_id': 'template-stale-file'}, 'template-stale-file-row-create')
    assert authority.read_document('owner', 'page-e1')['document_sequence'] == before
    assert not any(row['id'] == 'template-stale-file-row' for row in
                   authority.read_database('owner', 'database-e1')['rows'])

    command('database.template.create', 'template-file-link',
            {'name': 'Linked file', 'body': [], 'default_values': {
                'template-files': [{'url': 'https://example.com/brief.pdf',
                                    'name': 'Brief'}]}},
            'template-file-link-create', schema=True)
    linked = command('database.row.create', 'template-file-link-row',
                     {'template_id': 'template-file-link'},
                     'template-file-link-row-create')
    assert linked['applied_defaults']['template-files'] == [
        {'url': 'https://example.com/brief.pdf', 'name': 'Brief'}]
    def set_file_cell(value, operation):
        page = authority.read_document('owner', 'page-e1')
        row = next(item for item in authority.read_database('owner', 'database-e1')['rows']
                   if item['id'] == 'template-file-link-row')
        return authority.execute_typed_command(
            'owner', 'page-e1', command_kind='database.cell.set',
            target_id='template-file-link-row:template-files',
            payload={'row_id': 'template-file-link-row', 'property_id': 'template-files',
                     'value': value},
            expected_document_sequence=page['document_sequence'],
            expected_workspace_epoch=page['workspace_epoch'],
            expected_document_epoch=page['document_epoch'],
            expected_target_version=row['cell_versions']['template-files'],
            operation_id=operation)
    set_file_cell([{'file_id': file_id}], 'template-direct-uploaded-file')
    set_file_cell([{'url': 'https://example.com/agenda.pdf', 'name': 'Agenda'}],
                  'template-direct-file-link')
    before = authority.read_document('owner', 'page-e1')['document_sequence']
    with pytest.raises(NotFoundOrDenied):
        set_file_cell([{'file_id': 'missing-file'}], 'template-direct-missing-file')
    assert authority.read_document('owner', 'page-e1')['document_sequence'] == before
    _cross_destination(store, _workspace_id)
    foreign_file = seed_copy_file(store, _workspace_id, file_id, 'page-e2',
                                  id='template-foreign-file')
    before = authority.read_document('owner', 'page-e1')['document_sequence']
    with pytest.raises(NotFoundOrDenied):
        set_file_cell([{'file_id': foreign_file}], 'template-direct-foreign-file')
    assert authority.read_document('owner', 'page-e1')['document_sequence'] == before
    for index, invalid in enumerate(('javascript:alert(1)',
                    'https://user:pass@example.com/file',
                    'https://example.com\\@elsewhere/file')):
        with pytest.raises(ValueError, match='safe URL'):
            set_file_cell([{'url': invalid, 'name': 'Unsafe'}],
                          'template-direct-unsafe-link-' + str(index))
        assert authority.read_document('owner', 'page-e1')['document_sequence'] == before
    assert next(item for item in authority.read_database('owner', 'database-e1')['rows']
                if item['id'] == 'template-file-link-row')['cells']['template-files'] == [
                    {'url': 'https://example.com/agenda.pdf', 'name': 'Agenda'}]
    for invalid in ('javascript:alert(1)', 'https://user:pass@example.com/file',
                    'https://example.com\\@elsewhere/file'):
        before = authority.read_document('owner', 'page-e1')['document_sequence']
        with pytest.raises(ValueError, match='safe URL'):
            command('database.template.create', 'template-unsafe-file-link',
                    {'name': 'Unsafe', 'body': [], 'default_values': {
                        'template-files': [{'url': invalid, 'name': 'Unsafe'}]}},
                    'template-unsafe-file-link-' + str(before), schema=True)
        assert authority.read_document('owner', 'page-e1')['document_sequence'] == before


@pytest.mark.regression
def test_ui_r5_owned_database_copy_detaches_external_inverse_in_committed_catalog(
        placement_environment):
    store, workspace_id = placement_environment
    _cross_destination(store, workspace_id)
    authority = store.iam
    page = authority.read_document("owner", "page-e1")
    if not any(block.get("props", {}).get("database_id") == "database-e1"
               for block in page["blocks"]):
        authority.execute_typed_command(
            "owner", "page-e1", command_kind="block.insert",
            target_id="relation-copy-source-block",
            payload={"type": "table", "content": "", "props": {"database_id": "database-e1"},
                     "after_block_id": None},
            expected_document_sequence=page["document_sequence"],
            expected_workspace_epoch=page["workspace_epoch"],
            expected_document_epoch=page["document_epoch"],
            operation_id="relation-copy-source-block-insert")
    with authority._transaction() as con:
        con.execute(
            "INSERT INTO workspace_collaboration_database_properties("
            "id,database_id,name,property_type,position,config_json,"
            "row_version,created_at,updated_at) VALUES("
            "'relation-copy-source','database-e1','Related','relation',3,?,1,1,1)",
            (json.dumps({"target_database_id": "database-e2"}),))
        con.execute(
            "INSERT INTO workspace_collaboration_database_rows("
            "id,database_id,position,row_version,created_at,updated_at) "
            "VALUES('relation-copy-target-row','database-e2',0,1,1,1)")
        con.execute(
            "INSERT INTO workspace_collaboration_database_cells("
            "row_id,property_id,value_json,row_version,updated_at) "
            "VALUES('row-e1','relation-copy-source',?,1,1)",
            (json.dumps(["relation-copy-target-row"]),))
    source_page = authority.read_document("owner", "page-e1")
    target_page = authority.read_document("owner", "page-e2")
    enable_relation_pair(
        authority, actor_subject_id="owner", workspace_id=workspace_id,
        source_document_id="page-e1", source_database_id="database-e1",
        source_property_id="relation-copy-source", target_document_id="page-e2",
        target_database_id="database-e2", inverse_property_id="relation-copy-back",
        inverse_name="Back", expected_source_sequence=source_page["document_sequence"],
        expected_target_sequence=target_page["document_sequence"],
        expected_workspace_epoch=source_page["workspace_epoch"],
        expected_source_epoch=source_page["document_epoch"],
        expected_target_epoch=target_page["document_epoch"],
        expected_source_property_version=1, operation_id="relation-copy-enable")

    source_page = authority.read_document("owner", "page-e1")
    fences = dict(expected_document_sequence=source_page["document_sequence"],
        expected_workspace_epoch=source_page["workspace_epoch"],
        expected_document_epoch=source_page["document_epoch"])
    result = copy_owned_database(
        authority, "owner", workspace_id, "page-e1", "database-e1",
        operation_id="relation-copy-owned", **fences)
    copied = authority.read_database("owner", result["database_id"])
    relation = next(item for item in copied["properties"]
                    if item["name"] == "Related")
    assert relation["config"] == {"target_database_id": "database-e2"}
    assert copied["rows"][0]["cells"][relation["id"]] == ["relation-copy-target-row"]
    assert copied["rows"][0]["id"] != "row-e1"
    original = authority.read_database("owner", "database-e1")
    source_relation = next(item for item in original["properties"]
                           if item["id"] == "relation-copy-source")
    assert source_relation["config"]["reciprocal_property_id"] == "relation-copy-back"
    target = authority.read_database("owner", "database-e2")
    assert target["rows"][0]["cells"]["relation-copy-back"] == ["row-e1"]
    assert copy_owned_database(
        authority, "owner", workspace_id, "page-e1", "database-e1",
        operation_id="relation-copy-owned", **fences)["replayed"] is True

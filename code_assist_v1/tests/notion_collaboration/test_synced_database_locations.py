"""UI-SYNC-LOCATIONS-DB: exact body locations and live template boundaries."""
import json
import pytest
from code_assist_v1.notion_collaboration import synced_locations, synced_blocks
from code_assist_v1.tests.notion_collaboration.test_synced_database_body_read import (
    environment,body_environment,team_environment,file_environment,member_environment,
    native_environment,archive_environment,guest_environment,image_environment,row_body,insert,dump,
)
from code_assist_v1.tests.notion_collaboration.test_database_features import _schema
from code_assist_v1.tests.notion_collaboration.test_synced_database_body_identity import original,reference

@pytest.mark.parametrize('kind',['row','template'])
def test_inventory_reports_main_row_template_and_nested_endpoints_with_current_metadata(environment,kind):
    store,ws=environment
    origin=original('db-original')
    nested={'id':'holder','type':'toggle','content':'root','props':{'children':[reference('nested-peer','page-e1','db-original')]}}
    row=[origin if kind=='row' else reference('row-peer','page-e1','db-original'),nested]
    template=[origin if kind=='template' else reference('template-peer','page-e1','db-original')]
    row_body(store,row)
    _schema(store.iam,'database.template.create','location-template',{'name':'검증 템플릿','body':template,'default_values':{}},'locations-template-create')
    insert(store,'main-peer',reference('main-peer','page-e1','db-original'))
    before=dump(store);value=synced_locations.read_synced_locations(store.iam,'owner','page-e1','main-peer',workspace_id=ws)
    assert value['other_count']==3 and len(value['entries'])==4
    source=next(e for e in value['entries'] if e['original'])
    assert source['location']['kind']==kind
    templated=next(e for e in value['entries'] if e['location']['kind']=='template')
    assert templated['body_title']=='검증 템플릿' and templated['location']['template_id']=='location-template'
    assert next(e for e in value['entries'] if e['endpoint']['block_id']=='nested-peer')['location']=={'kind':'row','database_id':'database-e1','row_id':'row-e1'}
    assert '공유 내용' not in json.dumps(value) and dump(store)==before
    if kind=='row':
        _schema(store.iam,'database.template.archive','location-template',{},'locations-template-archive',target_version=1)
        latest=synced_locations.read_synced_locations(store.iam,'owner','page-e1','main-peer',workspace_id=ws)
        assert latest['other_count']==2 and 'location-template' not in json.dumps(latest)

def test_inventory_does_not_guess_ambiguous_endpoint_across_body_namespaces(environment):
    store,ws=environment;row_body(store,[original('db-original'),reference('ambiguous-peer','page-e1','db-original')])
    insert(store,'main-peer',reference('main-peer','page-e1','db-original'))
    _schema(store.iam,'database.template.create','ambiguous-template',{'name':'Other','body':[{'id':'ambiguous-peer','type':'paragraph','content':'ordinary','props':{}}],'default_values':{}},'locations-collision-template')
    before=dump(store)
    with pytest.raises(synced_blocks.SyncedShapeUnavailable,match='ambiguous'):
        synced_locations.read_synced_locations(store.iam,'owner','page-e1','main-peer',workspace_id=ws)
    assert dump(store)==before

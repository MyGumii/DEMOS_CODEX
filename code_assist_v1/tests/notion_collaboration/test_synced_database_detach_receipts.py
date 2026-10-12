"""UI-SYNC-DB-DETACH-RECEIPT: current access, body trash and ID boundaries."""
import pytest

from code_assist_v1.notion_collaboration import database_row_trash, synced_detach_service as service
from code_assist_v1.notion_collaboration import synced_detach_plan as plan, synced_blocks as synced
from code_assist_v1.notion_collaboration.canonical_authority import NotFoundOrDenied, _stable_id
from code_assist_v1.tests.notion_collaboration.test_synced_database_detach import (
    environment, body_environment, team_environment, file_environment, member_environment,
    native_environment, archive_environment, guest_environment, image_environment,
    seed,payload,run,dump,_schema,request,block,
)
from code_assist_v1.tests.notion_collaboration.test_synced_database_text_fences import sharing_environment


def archive_body(store,ws,kind):
    if kind=='template':
        value=next(item for item in store.iam.read_database('owner','database-e1')['templates'] if item['id']=='detach-template')
        _schema(store.iam,'database.template.archive','detach-template',{},'detach-receipt-template-archive',actor='owner',target_version=value['version'])
    else:
        with store.iam._reader() as con:
            page=store.iam._document(con,'page-e1');workspace=store.iam._workspace(con,ws)
            db=con.execute("SELECT row_version FROM workspace_collaboration_databases WHERE id='database-e1'").fetchone()
            row=con.execute("SELECT row_version FROM workspace_collaboration_database_rows WHERE id='row-e1'").fetchone()
        database_row_trash.set_database_row_trashed(store.iam,'owner',ws,'page-e1','database-e1','row-e1',archived=True,
            operation_id='detach-receipt-row-archive',expected_document_sequence=page['document_sequence'],
            expected_workspace_epoch=workspace['security_epoch'],expected_document_epoch=page['access_epoch'],
            expected_database_version=db['row_version'],expected_row_version=row['row_version'])


@pytest.mark.parametrize('kind',['row','template'])
@pytest.mark.parametrize('owner',[True,False])
def test_archived_body_receipt_requires_current_workspace_owner_without_source_recapture(sharing_environment,kind,owner):
    store,ws=sharing_environment;seed(store,'page',kind)
    actor='owner' if owner else 'content-editor' if kind=='row' else 'schema-editor'
    value=payload(store,actor=actor);saved=run(store,ws,value,actor=actor)
    archive_body(store,ws,kind);before=dump(store)
    if owner:
        receipt=service.read_synced_detach_result(store.iam,actor,ws,'page-e1','detach-peer',**value)
        assert receipt['state']=='committed' and receipt['current']['body_archived'] is True
        assert receipt['result']=={**saved,'replayed':True}
        assert run(store,ws,value,actor=actor)=={**saved,'replayed':True}
    else:
        with pytest.raises(NotFoundOrDenied):service.read_synced_detach_result(store.iam,actor,ws,'page-e1','detach-peer',**value)
        with pytest.raises(NotFoundOrDenied):run(store,ws,value,actor=actor)
    assert dump(store)==before


@pytest.mark.parametrize('kind',['row','template'])
@pytest.mark.parametrize('access',['view','revoked'])
def test_saved_body_receipt_uses_current_destination_view_authority(environment,kind,access):
    store,ws=environment;seed(store,'page',kind);actor='content-editor' if kind=='row' else 'schema-editor'
    value=payload(store,actor=actor);saved=run(store,ws,value,actor=actor)
    current=store.iam.read_document('owner','page-e1')
    if access=='view':
        store.iam.set_page_grant('owner','page-e1','subject',actor,role='view',inherit_to_descendants=False,
            expected_workspace_epoch=current['workspace_epoch'],expected_document_epoch=current['document_epoch'],operation_id='detach-receipt-view-only')
    else:
        store.iam.revoke_page_grant('owner','page-e1','subject',actor,expected_workspace_epoch=current['workspace_epoch'],
            expected_document_epoch=current['document_epoch'],operation_id='detach-receipt-revoked')
    before=dump(store)
    if access=='view':assert run(store,ws,value,actor=actor)=={**saved,'replayed':True}
    else:
        with pytest.raises(NotFoundOrDenied):run(store,ws,value,actor=actor)
    assert dump(store)==before


def test_allocated_detach_id_cannot_collide_with_an_unrelated_template_body(environment):
    store,ws=environment;seed(store,'page','row');operation='detach-cross-body-collision'
    identity=_stable_id('block',plan.CONTRACT,'owner',ws,'page-e1','detach-peer',operation,'page-e1','detach-original-child')
    _schema(store.iam,'database.template.create','unrelated-collision-template',{'name':'Unrelated','body':[block(identity,content='원래 항목')],'default_values':{}},'detach-collision-template')
    value=payload(store,operation=operation);before=dump(store)
    with pytest.raises(synced.SyncedShapeUnavailable,match='identity collision'):run(store,ws,value)
    assert dump(store)==before

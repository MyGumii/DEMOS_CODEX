"""UI-SYNC-LINKED-TEMPLATE: exact retention, parent authority and atomic replay."""
from copy import deepcopy
import multiprocessing

import pytest

from code_assist_v1.notion_collaboration import linked_databases as linked, database_access
from code_assist_v1.notion_collaboration.linked_template_body import retained_body
from code_assist_v1.notion_collaboration.canonical_authority import InjectedFailure, NotFoundOrDenied, OperationConflict, EpochConflict
from code_assist_v1.notion_collaboration.database_row_content import clean_blocks
from code_assist_v1.notion_collaboration.iam_authority import InMemoryIdentityDirectory
from code_assist_v1.notion_collaboration.workspace_image_authority import WorkspaceImageAuthority
from code_assist_v1.tests.notion_collaboration.test_database_sharing_migration import (
    icon_batch_environment, activity_environment, environment, appearance_environment,
    body_environment, team_environment, file_environment, member_environment,
    native_environment, archive_environment, guest_environment, image_environment,
    placement, custom_emoji, item_layout, template_default, migration, upload_scope_migration,
)
from code_assist_v1.tests.notion_collaboration.test_database_features import _apply, _schema, SUBJECTS
from code_assist_v1.tests.notion_collaboration.test_linked_database_copy import setup_pair
from code_assist_v1.tests.notion_collaboration.test_file_copy_provenance import dump
from code_assist_v1.tests.notion_collaboration.test_synced_database_body_identity import original, reference


@pytest.fixture()
def linked_template_environment(icon_batch_environment):
    store,workspace=icon_batch_environment
    document,_title=setup_pair(store,workspace)
    for index,target in enumerate([placement,custom_emoji,item_layout,template_default,migration,upload_scope_migration]):
        _apply(target,store.db_path,store.user_data_root,f'before-linked-template-retention-{index}.db')
    _schema(store.iam,'database.template.create','retention-template',
        {'name':'Retained source','body':[original('retained-original'),reference('retained-peer','page-e1','retained-original'),
            {'id':'ordinary','type':'paragraph','content':'A','props':{}}],'default_values':{}},
        'retention-template-create',actor='owner',target_version=0)
    return store,workspace,document


def value(store,workspace,document,actor='owner'):
    data=linked.read_linked_database_scoped(store.iam,actor,workspace,document,'linked-block')
    template=next(item for item in data['database']['templates'] if item['id']=='retention-template')
    body=deepcopy(template['body']);body[-1]['content']='B'
    return dict(source=data['source'],instance_witness=data['instance_witness'],source_access=data['access'],
        name='Retained renamed',target_id=template['id'],body=body,default_values={},
        expected_target_version=template['version'],operation_id='retention-template-save')


def save(authority,workspace,document,payload,actor='owner',**kw):
    return linked.execute_linked_template_update_scoped(authority,actor,workspace,document,'linked-block',**payload,**kw)


@pytest.mark.integration
@pytest.mark.regression
def test_retained_synced_body_keeps_exact_endpoints_and_replays_one_atomic_receipt(linked_template_environment):
    store,workspace,document=linked_template_environment;payload=value(store,workspace,document)
    result=save(store.iam,workspace,document,payload);after=dump(store)
    assert save(store.iam,workspace,document,payload)==result and dump(store)==after
    template=next(item for item in store.iam.read_database('owner','database-e1')['templates'] if item['id']=='retention-template')
    assert template['version']==payload['expected_target_version']+1
    assert template['body']==clean_blocks(payload['body']) and template['body'][1]['content']==''
    with pytest.raises(OperationConflict):save(store.iam,workspace,document,{**payload,'name':'different'})
    assert dump(store)==after


@pytest.mark.parametrize('change',['source_text','child_text','retarget','remove','move','add','page_link'])
def test_retention_rejects_new_removed_moved_or_modified_protected_subtrees(change):
    body=[original('original'),reference('peer','page-e1','original'),{'id':'ordinary','type':'paragraph','content':'A','props':{}}]
    proposed=deepcopy(body)
    if change=='source_text':proposed[0]['content']='changed'
    elif change=='child_text':proposed[0]['props']['children'][0]['content']='changed'
    elif change=='retarget':proposed[1]['props']['synced_from']['block_id']='another'
    elif change=='remove':proposed.pop(1)
    elif change=='move':proposed[0],proposed[1]=proposed[1],proposed[0]
    elif change=='add':proposed.append(reference('new','page-e1','original'))
    else:proposed[-1]['props']['document_id']='private-page'
    assert retained_body(proposed,body)[0] is False
    proposed=deepcopy(body);proposed[-1]['content']='ordinary edit'
    assert retained_body(proposed,body)==(True,True)


@pytest.mark.integration
@pytest.mark.regression
@pytest.mark.parametrize('fault',['after_domain','after_audit','after_outbox'])
def test_retained_body_save_rolls_back_domain_receipt_audit_and_outbox(linked_template_environment,fault):
    store,workspace,document=linked_template_environment;payload=value(store,workspace,document);before=dump(store)
    with pytest.raises(InjectedFailure):save(store.iam,workspace,document,payload,fault=fault)
    assert dump(store)==before


@pytest.mark.integration
@pytest.mark.regression
def test_scoped_writer_rejects_endpoint_changes_and_page_reference_injection_atomically(linked_template_environment):
    store,workspace,document=linked_template_environment;payload=value(store,workspace,document);before=dump(store)
    for mode in ['source','remove','page']:
        proposed=deepcopy(payload);proposed['operation_id']='retention-denied-'+mode
        if mode=='source':proposed['body'][0]['props']['children'][0]['content']='unscoped mutation'
        elif mode=='remove':proposed['body'].pop(1)
        else:proposed['body'][-1]['props']['document_id']='private-page'
        with pytest.raises(NotFoundOrDenied):save(store.iam,workspace,document,proposed)
        assert dump(store)==before


@pytest.mark.integration
@pytest.mark.regression
def test_replay_after_parent_revoke_never_discloses_a_saved_synced_body(linked_template_environment):
    store,workspace,document=linked_template_environment;actor='retention-editor'
    store.iam.identity_directory.replace([*SUBJECTS,actor])
    access=database_access.resolve(store.iam,'owner',workspace,'database-e1')
    database_access.change_grant(store.iam,'owner',workspace,'database-e1','subject',actor,role='edit',
        expected_workspace_epoch=access.workspace_epoch,expected_document_epoch=access.document_epoch,
        expected_database_epoch=access.database_epoch,operation_id='retention-editor-db-grant')
    for target in ['page-e1',document]:
        current=store.iam.read_document('owner',target)
        store.iam.set_page_grant('owner',target,'subject',actor,role='edit',inherit_to_descendants=False,
            expected_workspace_epoch=current['workspace_epoch'],expected_document_epoch=current['document_epoch'],operation_id='retention-editor-page-'+target)
    payload=value(store,workspace,document,actor);save(store.iam,workspace,document,payload,actor)
    current=store.iam.read_document('owner','page-e1')
    store.iam.revoke_page_grant('owner','page-e1','subject',actor,expected_workspace_epoch=current['workspace_epoch'],
        expected_document_epoch=current['document_epoch'],operation_id='retention-editor-parent-revoke')
    before=dump(store)
    # Refresh authority epochs in the retry would change the exact request hash;
    # the original receipt still requires current source-page read authority.
    with pytest.raises((NotFoundOrDenied,EpochConflict)):
        save(store.iam,workspace,document,payload,actor)
    assert dump(store)==before


def _worker(catalog,root,workspace,document,payload,ready,start,barrier,results):
    authority=WorkspaceImageAuthority.open(catalog,root,InMemoryIdentityDirectory(['owner']))
    prepare=linked._scoped_write_scope
    def together(*args,**kw):
        scope=prepare(*args,**kw);barrier.wait(timeout=30);return scope
    linked._scoped_write_scope=together;ready.put(True)
    if not start.wait(30):results.put({'error':'start timeout'});return
    try:results.put(save(authority,workspace,document,payload))
    except Exception as error:results.put({'error':type(error).__name__,'message':str(error)})


@pytest.mark.integration
@pytest.mark.regression
def test_two_process_retained_template_save_commits_one_identical_receipt(linked_template_environment):
    store,workspace,document=linked_template_environment;payload=value(store,workspace,document)
    context=multiprocessing.get_context('spawn');ready,results,start,barrier=context.Queue(),context.Queue(),context.Event(),context.Barrier(2)
    workers=[context.Process(target=_worker,args=(store.db_path,store.user_data_root,workspace,document,payload,ready,start,barrier,results)) for _ in range(2)]
    try:
        for worker in workers:worker.start()
        assert ready.get(timeout=30) and ready.get(timeout=30);start.set()
        receipts=[results.get(timeout=40),results.get(timeout=40)]
        for worker in workers:worker.join(30);assert worker.exitcode==0
        assert all('error' not in item for item in receipts),receipts
        assert receipts[0]==receipts[1]
        template=next(item for item in store.iam.read_database('owner','database-e1')['templates'] if item['id']=='retention-template')
        assert template['version']==payload['expected_target_version']+1 and template['body']==clean_blocks(payload['body'])
    finally:
        start.set()
        for worker in workers:
            if worker.is_alive():worker.terminate()
            worker.join(5)
        ready.close();results.close()

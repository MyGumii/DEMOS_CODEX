"""UI-SYNC-DB-EDIT-FENCE: dual-page authority, history and scoped DB epochs."""
import pytest

from code_assist_v1.notion_collaboration import synced_blocks as synced, synced_edit as edit, synced_database_edit as body_edit, database_access
from code_assist_v1.notion_collaboration.canonical_authority import NotFoundOrDenied
from code_assist_v1.notion_collaboration.history_authority import HistoryIntegrityError
from code_assist_v1.notion_collaboration.page_settings import update_page_settings
from code_assist_v1.tests.notion_collaboration.test_synced_database_text_edit import (
    environment, body_environment, team_environment, file_environment, member_environment,
    native_environment, archive_environment, guest_environment, image_environment,
    seed, payload, run, dump, source_value, request,
)
from code_assist_v1.tests.notion_collaboration.test_synced_edit import grant_edit
from code_assist_v1.tests.notion_collaboration.test_synced_copy_references import insert_synced
from code_assist_v1.tests.notion_collaboration.test_native_page_creation import create
from code_assist_v1.tests.notion_collaboration.test_database_row_content import checkpoint, restore
from code_assist_v1.tests.notion_collaboration.test_database_features import _apply
from code_assist_v1 import notion_workspace_same_document_file_copy_migration as same_file
from code_assist_v1 import notion_workspace_database_activity_migration as activity
from code_assist_v1 import notion_workspace_database_trash_migration as db_trash
from code_assist_v1 import notion_workspace_database_row_trash_migration as row_trash
from code_assist_v1 import notion_workspace_database_row_page_placement_migration as placement
from code_assist_v1 import notion_workspace_custom_emoji_migration as emoji
from code_assist_v1 import notion_workspace_database_item_page_layout_migration as layout
from code_assist_v1 import notion_workspace_database_template_default_migration as default_template
from code_assist_v1 import notion_workspace_database_sharing_migration as sharing


def cross_page(store, ws):
    seed(store)
    child = create(store, ws, operation="db-edit-reference-page")["page"]["id"]
    insert_synced(store, child, "cross-peer", origin={"document_id":"page-e1","block_id":"edit-original"})
    return child


def cross_payload(store, child):
    current = synced.read_synced_block(store.iam,"owner",child,"cross-peer")
    return {**payload(store),"origin":current["origin"],"witnesses":current["witnesses"]}


def cross_run(store, ws, child, value):
    return edit.edit_synced_text(store.iam,"outsider",ws,child,"cross-peer",**value)


@pytest.mark.parametrize("source_role,instance_role",[("view","edit_content"),("edit_content","view")])
def test_database_origin_requires_write_authority_on_both_pages(environment,source_role,instance_role):
    store,ws = environment; child = cross_page(store,ws)
    grant_edit(store,"page-e1",source_role,"db-text-grant-origin")
    grant_edit(store,child,instance_role,"db-text-grant-peer")
    assert not synced.read_synced_block(store.iam,"outsider",child,"cross-peer")["capabilities"]["edit_text"]
    value = cross_payload(store,child); before=dump(store)
    with pytest.raises(NotFoundOrDenied): cross_run(store,ws,child,value)
    assert dump(store)==before


@pytest.mark.parametrize("when",["prepare","retry"])
def test_revocation_blocks_prepared_body_write_and_saved_receipt_disclosure(environment,monkeypatch,when):
    store,ws = environment; child = cross_page(store,ws)
    for document in ("page-e1",child): grant_edit(store,document,"edit_content","db-text-edit-"+document)
    value = cross_payload(store,child); after=[]
    def revoke():
        current=store.iam.read_document("owner","page-e1")
        store.iam.revoke_page_grant("owner","page-e1","subject","outsider",
            expected_workspace_epoch=current["workspace_epoch"],expected_document_epoch=current["document_epoch"],
            operation_id="db-text-revoke-origin")
        after.append(dump(store))
    if when=="prepare":
        prepare=body_edit.prepare
        def raced(*args):
            result=prepare(*args); revoke(); return result
        monkeypatch.setattr(body_edit,"prepare",raced)
    else:
        assert cross_run(store,ws,child,value)["storage"]=="database_body"
        revoke()
    with pytest.raises(NotFoundOrDenied): cross_run(store,ws,child,value)
    assert dump(store)==after[0]


def test_locked_reference_page_cannot_mutate_unlocked_database_origin(environment):
    store,ws=environment; child=cross_page(store,ws)
    value=cross_payload(store,child)
    update_page_settings(store.iam,"owner",ws,child,updates={"locked":True},
        operation_id="db-text-lock-peer",**request(store,child))
    before=dump(store)
    assert not synced.read_synced_block(store.iam,"owner",child,"cross-peer")["capabilities"]["edit_text"]
    with pytest.raises(PermissionError):
        edit.edit_synced_text(store.iam,"owner",ws,child,"cross-peer",**value)
    assert dump(store)==before


@pytest.mark.parametrize("kind",["row","template"])
def test_database_synced_text_checkpoint_and_existing_restore_boundary(environment,kind):
    store,ws=environment; seed(store,kind)
    original_body=source_value(store,kind)[1]
    first=checkpoint(store.iam,"db-text-history-before")
    result=run(store,ws,payload(store))
    edited_body=source_value(store,kind)[1]
    saved=checkpoint(store.iam,"db-text-history-after")
    revision=store.iam.get_revision("owner","page-e1",saved["revision"])["snapshot"]
    database=revision["databases"][0]
    value=database["rows"][0]["body"]["blocks"] if kind=="row" else database["templates"][0]["body"]
    assert value==edited_body
    if kind=="template":
        # Template structure is outside the existing row-body history restorer.
        # Preserve that explicit all-or-nothing boundary until its own successor.
        before=dump(store)
        with pytest.raises(HistoryIntegrityError,match="database history restore is unavailable"):
            restore(store.iam,first["revision"],"db-text-history-template-refused")
        assert dump(store)==before and source_value(store,kind)[1]==edited_body
        return
    restore(store.iam,first["revision"],"db-text-history-restore-before")
    assert source_value(store,kind)[1]==original_body
    restore(store.iam,saved["revision"],"db-text-history-restore-after")
    assert source_value(store,kind)[1]==edited_body
    assert source_value(store,kind)[0]["version"]>result["target_version"]
    assert synced.read_synced_block(store.iam,"owner","page-e1","edit-peer")["tree"]["children"][0]["content"]=="수정한 자식 😀"


@pytest.fixture()
def sharing_environment(environment):
    store,ws=environment
    for migration in (same_file,activity,db_trash,row_trash,placement,emoji,layout,default_template,sharing):
        _apply(migration,store.db_path,store.user_data_root,f"db-text-fence-before-{migration.TARGET_VERSION}.db")
    return store,ws


def db_grant(store,ws,role,operation):
    access=database_access.resolve(store.iam,"owner",ws,"database-e1")
    return database_access.change_grant(store.iam,"owner",ws,"database-e1","subject","outsider",
        role=role,expected_workspace_epoch=access.workspace_epoch,expected_document_epoch=access.document_epoch,
        expected_database_epoch=access.database_epoch,operation_id=operation)


def test_direct_database_edit_grant_cannot_disclose_or_mutate_page_scoped_synced_body(sharing_environment):
    store,ws=sharing_environment; seed(store); value=payload(store)
    db_grant(store,ws,"full_access","db-text-direct-grant")
    current=store.iam.read_document("owner","page-e1")
    store.iam.revoke_page_grant("owner","page-e1","subject","outsider",
        expected_workspace_epoch=current["workspace_epoch"],expected_document_epoch=current["document_epoch"],
        operation_id="db-text-direct-no-page-grant")
    assert database_access.resolve(store.iam,"outsider",ws,"database-e1").role=="full_access"
    before=dump(store)
    with pytest.raises(NotFoundOrDenied): synced.read_synced_block(store.iam,"outsider","page-e1","edit-peer")
    with pytest.raises(NotFoundOrDenied): run(store,ws,value,actor="outsider")
    assert dump(store)==before


def test_database_grant_epoch_changed_during_prepare_fences_owner_write(sharing_environment,monkeypatch):
    store,ws=sharing_environment; seed(store); value=payload(store); prepare=body_edit.prepare; after=[]
    def raced(*args):
        result=prepare(*args); db_grant(store,ws,"view","db-text-late-database-grant"); after.append(dump(store)); return result
    monkeypatch.setattr(body_edit,"prepare",raced)
    with pytest.raises(synced.SyncedSourceChanged): run(store,ws,value)
    assert dump(store)==after[0]


@pytest.mark.parametrize("kind",["row","template"])
def test_current_database_authority_write_records_activity_and_content_free_updates(sharing_environment,kind):
    from code_assist_v1.notion_collaboration.database_updates import read_database_updates_scoped
    store,ws=sharing_environment; seed(store,kind)
    result=run(store,ws,payload(store)); after=dump(store)
    with store.iam._reader() as con:
        activity=con.execute("SELECT * FROM workspace_collaboration_database_activity WHERE database_id='database-e1'").fetchone()
        assert activity["last_editor_subject_id"]=="owner" and activity["document_sequence"]==result["document_sequence"]
        doc=store.iam._document(con,"page-e1")
        from code_assist_v1.notion_collaboration.canonical_authority import _canonical_json, _sha256
        assert doc["content_sha256"]==_sha256(_canonical_json(store.iam._editing_snapshot(con,doc)))
        assert not con.execute("PRAGMA foreign_key_check").fetchall()
    updates=read_database_updates_scoped(store.iam,"owner",ws,"database-e1")
    event=next(item for item in updates["items"] if item["document_sequence"]==result["document_sequence"])
    assert event["command_kind"]==result["command_kind"]
    if kind=="row": assert event["row_id"]=="row-e1"
    assert "수정한 자식" not in str(updates) and "edit-original" not in str(updates) and "page-e1" not in str(updates)
    assert dump(store)==after

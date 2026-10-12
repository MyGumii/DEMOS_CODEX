"""UI-TEAM-CREATE: atomic location, inherited access and lifecycle boundaries."""
import multiprocessing
import sqlite3

import pytest

from code_assist_v1 import notion_workspace_native_team_migration as migration
from code_assist_v1.notion_collaboration import linked_databases, page_creation, page_move, page_copy
from code_assist_v1.notion_collaboration.canonical_authority import AuthorityUnavailable, NotFoundOrDenied, OperationConflict, InjectedFailure
from code_assist_v1.notion_collaboration.iam_runtime import CollaborativeWorkspaceStore
from code_assist_v1.tests.notion_collaboration.test_file_copy_provenance import (
    environment as file_environment, member_environment, native_environment, archive_environment,
    guest_environment, image_environment, _apply, _backup, dump,
)
from code_assist_v1.tests.notion_collaboration.test_page_move import current_body


@pytest.fixture()
def environment(file_environment):
    old, ws = file_environment
    _apply(migration, old.db_path, old.user_data_root, "before-native-team-2417.db")
    store = CollaborativeWorkspaceStore(old.db_path, user_data_root=old.user_data_root, identity_directory=old.identity_directory)
    return store, ws


def version(store, ws):
    return store.get_workspace("owner", ws)["version"]


def teams(store, ws):
    for subject in ("content-editor", "schema-editor"):
        store.set_member("owner", ws, subject, role="member", base_version=version(store, ws), operation_id="team-member-"+subject)
    result = []
    for index in (1, 2):
        team = store.create_teamspace("owner", ws, name=f"Team {index}", visibility="closed",
                                    base_version=version(store, ws), operation_id=f"team-create-{index}")["teamspace"]["id"]
        result.append(team)
        for subject in (("content-editor", "schema-editor") if index == 1 else ("schema-editor",)):
            store.set_teamspace_member("owner", ws, team, subject, role="member",
                base_version=version(store, ws), operation_id=f"team-join-{index}-"+subject)
    return result


def create(store, ws, team="", *, actor="owner", parent="", operation="team-root-create", base=None, **kw):
    return page_creation.create_page(store.iam, actor, ws, title="Team document", parent_id=parent, teamspace_id=team,
                                    base_version=base or version(store, ws), operation_id=operation, **kw)


def move(store, ws, document, *, actor="owner", operation="team-move", **destination):
    page = store.iam.read_document(actor, document)
    return page_move.move_page(store.iam, actor, ws, document, operation_id=operation,
        expected_document_sequence=page["document_sequence"], expected_workspace_epoch=page["workspace_epoch"],
        expected_document_epoch=page["document_epoch"], **destination)


def test_ui_team_create_forward_plan_is_exact_and_existing_data_unchanged(file_environment):
    store, ws = file_environment
    before = dump(store)
    with pytest.raises(AuthorityUnavailable):
        create(store, ws, "ts-not-installed")
    plan = migration.build_plan(store.db_path, store.user_data_root)
    assert dump(store) == before
    backup = _backup(store.db_path, store.user_data_root / "backups" / "team-plan.db")
    for options in ({"backup_confirmed":False}, {"catalog_quiesced_confirmed":False}, {"exact_plan_checksum":"sha256:"+"0"*64}):
        with pytest.raises(migration.MigrationRefused):
            migration.apply_plan(store.db_path, store.user_data_root, **{
                "exact_plan_checksum":plan["plan_checksum"], "backup_confirmed":True,
                "catalog_quiesced_confirmed":True, "backup_path":backup, **options})
        assert dump(store) == before
    migration.apply_plan(store.db_path, store.user_data_root, exact_plan_checksum=plan["plan_checksum"],
                         backup_confirmed=True, catalog_quiesced_confirmed=True, backup_path=backup)
    inserts = lambda lines:[line for line in lines if line.startswith('INSERT INTO "') and not line.startswith('INSERT INTO "workspace_schema_migrations"')]
    assert inserts(dump(store)) == inserts(before)
    assert migration.build_plan(store.db_path, store.user_data_root)["state"] == "already_current"


def test_ui_team_create_member_hierarchy_and_revoked_creator_do_not_bypass_team_access(environment):
    store, ws = environment; a, b = teams(store, ws)
    with store.iam._reader() as con:
        sources = [tuple(r) for r in con.execute("SELECT * FROM workspace_page_sources ORDER BY source_page_id")]
    root = create(store, ws, a, actor="content-editor")["page"]["id"]
    child = create(store, ws, parent=root, actor="schema-editor", operation="team-child")["page"]["id"]
    grandchild = create(store, ws, parent=child, actor="content-editor", operation="team-grandchild")["page"]["id"]
    for actor in ("content-editor", "schema-editor"):
        items = {item["id"]:item for item in page_creation.native_page_items(store.iam, actor, ws)}
        for doc in (root, child, grandchild):
            assert store.iam.resolve_access(actor, doc).role == "full_access"
            assert items[doc]["placement"]["teamspace_id"] == a
    assert any(block["props"].get("page_id") == child for block in current_body(store.iam, root)["blocks"])
    with store.iam._reader() as con:
        assert [tuple(r) for r in con.execute("SELECT * FROM workspace_page_sources ORDER BY source_page_id")] == sources
        assert con.execute("SELECT COUNT(*) FROM workspace_collaboration_native_team_roots").fetchone()[0] == 1
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []
    store.remove_teamspace_member("owner", ws, a, "content-editor", base_version=version(store, ws), operation_id="team-revoke-creator")
    for doc in (root, child, grandchild):
        with pytest.raises(NotFoundOrDenied): store.iam.read_document("content-editor", doc)
        assert store.iam.read_document("schema-editor", doc)["document_id"] == doc
    assert not page_creation.native_page_items(store.iam, "content-editor", ws)
    with pytest.raises(NotFoundOrDenied): create(store, ws, a, actor="content-editor", operation="revoked-create")
    with pytest.raises(NotFoundOrDenied): create(store, ws, b, actor="content-editor", operation="other-team")


def test_ui_team_linked_page_requires_current_source_access_for_create_and_read(environment):
    store, ws = environment
    team, _ = teams(store, ws)
    source = page_creation.create_page(store.iam, "owner", ws, title="Private source",
        parent_id="", template="database", base_version=version(store, ws), operation_id="team-link-source")
    source_page_id = source["page"]["id"]
    source_id = store.iam.read_document("owner", source_page_id)["blocks"][0]["props"]["database_id"]
    options = dict(template="linked_database", source_document_id=source_page_id, source_database_id=source_id)
    before = dump(store)
    with pytest.raises(NotFoundOrDenied):
        create(store, ws, team, actor="content-editor", operation="team-link-denied", **options)
    assert dump(store) == before

    source_document = store.iam.read_document("owner", source_page_id)
    store.iam.set_page_grant("owner", source_page_id, "subject", "content-editor", role="view",
        inherit_to_descendants=False, expected_workspace_epoch=source_document["workspace_epoch"],
        expected_document_epoch=source_document["document_epoch"], operation_id="team-link-source-grant")
    linked = create(store, ws, team, actor="content-editor", operation="team-link-allowed", **options)
    linked_page_id = linked["page"]["id"]
    block_id = store.iam.read_document("content-editor", linked_page_id)["blocks"][0]["id"]
    assert linked_databases.read_linked_database(store.iam, "content-editor", linked_page_id, block_id)["source"]["database_id"] == source_id
    with pytest.raises(NotFoundOrDenied):
        linked_databases.read_linked_database(store.iam, "schema-editor", linked_page_id, block_id)

    source_document = store.iam.read_document("owner", source_page_id)
    store.iam.revoke_page_grant("owner", source_page_id, "subject", "content-editor",
        expected_workspace_epoch=source_document["workspace_epoch"],
        expected_document_epoch=source_document["document_epoch"], operation_id="team-link-source-revoke")
    assert store.iam.read_document("content-editor", linked_page_id)["blocks"][0]["id"] == block_id
    with pytest.raises(NotFoundOrDenied):
        linked_databases.read_linked_database(store.iam, "content-editor", linked_page_id, block_id)


@pytest.mark.parametrize("fault", ["after_event", "after_audit", "after_outbox"])
def test_ui_team_create_atomic_failure_leaves_no_location_grant_or_document(environment, fault):
    store, ws = environment; a, _ = teams(store, ws); before = dump(store)
    with pytest.raises(InjectedFailure): create(store, ws, a, actor="content-editor", fault=fault)
    assert dump(store) == before


def test_ui_team_create_exact_retry_validation_and_commit_reauthorization(environment, monkeypatch):
    store, ws = environment; a, b = teams(store, ws); base = version(store, ws)
    first = create(store, ws, a, base=base)
    before = dump(store)
    assert {**create(store, ws, a, base=base), "replayed":False} == first
    with pytest.raises(OperationConflict): create(store, ws, b, base=base)
    with pytest.raises(ValueError): create(store, ws, a, parent=first["page"]["id"])
    with pytest.raises(NotFoundOrDenied): create(store, ws, a, actor="outsider", operation="guest-create")
    assert dump(store) == before
    original = page_move._prepare_deltas
    def revoke_after_prepare(*args):
        result = original(*args)
        store.remove_teamspace_member("owner", ws, a, "content-editor", base_version=version(store, ws), operation_id="during-create-revoke")
        return result
    monkeypatch.setattr(page_move, "_prepare_deltas", revoke_after_prepare)
    with pytest.raises(NotFoundOrDenied): create(store, ws, a, actor="content-editor", operation="racing-create")
    with store.iam._reader() as con:
        assert con.execute("SELECT COUNT(*) FROM workspace_collaboration_native_team_roots").fetchone()[0] == 1


def test_ui_team_move_between_teams_and_private_replaces_only_default_team_access(environment):
    store, ws = environment; a, b = teams(store, ws)
    root = create(store, ws, a)["page"]["id"]
    child = create(store, ws, parent=root, operation="moving-child")["page"]["id"]
    target = create(store, ws, b, operation="target-team-root")["page"]["id"]
    body = current_body(store.iam, root)
    move(store, ws, root, before_id=target)
    with pytest.raises(NotFoundOrDenied): store.iam.read_document("content-editor", root)
    assert store.iam.resolve_access("schema-editor", child).role == "full_access"
    entries = {item["id"]:item for item in page_creation.native_page_items(store.iam, "owner", ws)}
    assert entries[root]["placement"]["teamspace_id"] == entries[child]["placement"]["teamspace_id"] == b
    move(store, ws, root, parent_id="", operation="move-to-personal")
    for doc in (root, child):
        with pytest.raises(NotFoundOrDenied): store.iam.read_document("schema-editor", doc)
    assert current_body(store.iam, root)["blocks"] == body["blocks"]
    entries = {item["id"]:item for item in page_creation.native_page_items(store.iam, "owner", ws)}
    assert entries[root]["placement"]["scope"] == entries[child]["placement"]["scope"] == "private"


def test_ui_team_copy_retains_team_placement_and_fresh_default_grant(environment):
    store, ws = environment; a, _ = teams(store, ws)
    root = create(store, ws, a)["page"]["id"]
    child = create(store, ws, parent=root, operation="copy-team-child")["page"]["id"]
    current = store.iam.read_document("owner", root)
    request = dict(operation_id="copy-team-root",expected_document_sequence=current["document_sequence"],
                   expected_workspace_epoch=current["workspace_epoch"],expected_document_epoch=current["document_epoch"])
    result = page_copy.copy_page(store.iam, "owner", ws, root, **request)
    copied = result["page"]["id"]
    assert copied not in {root,child}
    assert store.iam.resolve_access("content-editor", copied).role == "full_access"
    entries = {item["id"]:item for item in page_creation.native_page_items(store.iam, "content-editor", ws)}
    assert entries[copied]["placement"]["teamspace_id"] == a
    assert page_copy.copy_page(store.iam, "owner", ws, root, **request)["page"]["id"] == copied


def _create_worker(catalog, root, ws, team, base, start, results):
    from code_assist_v1.notion_collaboration.iam_authority import InMemoryIdentityDirectory
    store = CollaborativeWorkspaceStore(catalog, user_data_root=root, identity_directory=InMemoryIdentityDirectory(["owner"]))
    start.wait(30)
    try: results.put(create(store, ws, team, base=base))
    except Exception as error: results.put({"error":repr(error)})


def test_ui_team_create_two_processes_commit_one_page_and_one_team_grant(environment):
    store, ws = environment; a, _ = teams(store, ws); base = version(store, ws)
    context = multiprocessing.get_context("spawn"); start=context.Event();results=context.Queue()
    workers=[context.Process(target=_create_worker,args=(store.db_path,store.user_data_root,ws,a,base,start,results)) for _ in range(2)]
    for worker in workers: worker.start()
    start.set()
    try:
        values=[results.get(timeout=50) for _ in workers]
        assert "error" not in values[0] and "error" not in values[1],values
        assert {**values[0],"replayed":False} == {**values[1],"replayed":False}
    finally:
        for worker in workers:
            worker.join(30)
            if worker.is_alive(): worker.terminate();worker.join()
    assert [worker.exitcode for worker in workers] == [0,0]
    with store.iam._reader() as con:
        assert con.execute("SELECT COUNT(*) FROM workspace_collaboration_native_team_roots").fetchone()[0] == 1
        assert con.execute("SELECT COUNT(*) FROM workspace_collaboration_access_grants WHERE document_id=?", (values[0]["page"]["id"],)).fetchone()[0] == 1

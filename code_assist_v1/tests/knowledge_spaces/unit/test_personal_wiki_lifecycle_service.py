from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
from uuid import UUID, uuid5

import pytest

from code_assist_v1.knowledge_core.agent_runtime import compose_canonical_agent_runtime
from code_assist_v1.knowledge_core.db import transaction
from code_assist_v1.knowledge_core.domain import FeatureFlags
from code_assist_v1.knowledge_core.repositories.personal_wikis import (
    PersonalWikiProfileRepository,
)
from code_assist_v1.knowledge_core.repositories.spaces import SpaceRepository
from code_assist_v1.knowledge_core.repositories.users import (
    LegacyUserRecord,
    UserRepository,
)
from code_assist_v1.knowledge_core.services.personal_wikis import (
    ArchivePersonalWikiCommand,
    CreatePersonalWikiCommand,
    ListPersonalWikisQuery,
    PersonalWikiConcealedError,
    PersonalWikiCursorError,
    PersonalWikiFailpointTriggered,
    PersonalWikiLifecycleService,
    PersonalWikiServiceDefaultRequiredError,
    PersonalWikiServiceIdempotencyError,
    PersonalWikiServiceNameConflictError,
    PersonalWikiServiceVersionError,
    RenamePersonalWikiCommand,
    RestorePersonalWikiCommand,
    SetDefaultPersonalWikiCommand,
)
from code_assist_v1.tests.knowledge_spaces.unit.test_migration_v10_semantic_graph_contracts import (
    _migrate_current,
)


pytestmark = pytest.mark.unit

_NAMESPACE = UUID("4c17cc91-0ae4-4fde-836e-74deed405db3")
_CURSOR_SECRET = b"sbe-2.1-personal-wiki-cursor-key-v1!!"


def _uuid(label: str) -> str:
    return str(uuid5(_NAMESPACE, label))


def _user(user_id: str, *, role: str = "user") -> LegacyUserRecord:
    return LegacyUserRecord(
        id=user_id,
        display_name=user_id.title(),
        password_hash=f"scrypt:sbe-2.1:{user_id}",
        system_role=role,
        account_status="active",
        created_at_ms=100,
        updated_at_ms=100,
    )


def _seed(factory, *users: tuple[str, str]) -> dict[str, str]:
    result: dict[str, str] = {}
    connection = factory.open()
    try:
        with transaction(connection):
            for index, (user_id, role) in enumerate(users, start=1):
                UserRepository().import_legacy(connection, _user(user_id, role=role))
                space_id = _uuid(f"default:{user_id}")
                SpaceRepository().ensure_personal(
                    connection,
                    space_id=space_id,
                    user_id=user_id,
                    created_at_ms=200 + index,
                )
                result[user_id] = space_id
    finally:
        connection.close()
    return result


def _service(factory) -> PersonalWikiLifecycleService:
    return PersonalWikiLifecycleService(factory, cursor_secret=_CURSOR_SECRET)


def _create(
    service: PersonalWikiLifecycleService,
    *,
    index: int,
    display_name: str | None = None,
    actor: str = "owner",
    expected_row_version: int = 1,
):
    return service.create(
        CreatePersonalWikiCommand(
            actor_user_id=actor,
            actor_scope_epoch=1,
            operation_id=_uuid(f"create:{actor}:{index}"),
            request_id=_uuid(f"create-request:{actor}:{index}"),
            display_name=display_name or f"Wiki {index:02d}",
            expected_row_version=expected_row_version,
        )
    )


def _counts(factory) -> dict[str, int]:
    connection = factory.open()
    try:
        return {
            name: int(connection.execute(f"SELECT count(*) FROM {name}").fetchone()[0])
            for name in (
                "audit_event",
                "conversation",
                "knowledge_edge",
                "knowledge_item",
                "knowledge_revision",
                "knowledge_space",
                "operation_dedup",
                "outbox_event",
                "personal_wiki_profile",
            )
        }
    finally:
        connection.close()


def _seed_wiki_identity_fixture(factory, *, space_id: str, label: str) -> None:
    connection = factory.open()
    try:
        with transaction(connection):
            conversation_id = _uuid(f"identity:{label}:conversation")
            connection.execute(
                """INSERT INTO conversation(
                     id,owner_user_id,space_id,captured_scope_epoch,title,status,
                     metadata_json,created_at,updated_at,row_version
                   ) VALUES (?,'owner',?,1,'Identity fixture','active','{}',300,300,1)""",
                (conversation_id, space_id),
            )
            items: list[tuple[str, str]] = []
            for index in (1, 2):
                item_id = _uuid(f"identity:{label}:item:{index}")
                revision_id = _uuid(f"identity:{label}:revision:{index}")
                body = f"# Identity fixture {label} {index}\n"
                digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
                connection.execute(
                    """INSERT INTO knowledge_item(
                         id,space_id,lineage_group_id,lifecycle_status,
                         curation_status,current_revision_id,
                         last_curation_transition_id,latest_deactivation_episode_id,
                         redirect_item_id,created_by,updated_by,created_at,
                         updated_at,row_version
                       ) VALUES (?,?,NULL,'active','provisional',?,NULL,NULL,NULL,
                         'owner','owner',300,300,1)""",
                    (item_id, space_id, revision_id),
                )
                connection.execute(
                    """INSERT INTO knowledge_revision(
                         id,item_id,revision_no,parent_revision_id,base_revision_id,
                         title,raw_content_sha256,normalized_content_sha256,
                         content_size_bytes,normalizer_version,metadata_json,
                         change_kind,change_reason,created_by,created_at
                       ) VALUES (?,?,1,NULL,NULL,?,?,?,?, 'markdown_lf_v1','{}',
                         'initial','identity fixture','owner',300)""",
                    (
                        revision_id,
                        item_id,
                        f"Identity {index}",
                        digest,
                        digest,
                        len(body.encode("utf-8")),
                    ),
                )
                connection.execute(
                    """INSERT INTO knowledge_revision_body(
                         revision_id,item_id,content_md,content_blob_hash,
                         content_size_bytes,content_sha256,created_at
                       ) VALUES (?,?,?,NULL,?,?,300)""",
                    (
                        revision_id,
                        item_id,
                        body,
                        len(body.encode("utf-8")),
                        digest,
                    ),
                )
                items.append((item_id, revision_id))
            connection.execute(
                """INSERT INTO knowledge_edge(
                     id,space_id,source_item_id,target_item_id,edge_type,
                     edge_status,origin,actor_kind,evidence_json,
                     needs_revalidation,created_by,updated_by,created_at,
                     updated_at,row_version
                   ) VALUES (?,?,?,?,'related','active','manual','user','{}',0,
                     'owner','owner',300,300,1)""",
                (
                    _uuid(f"identity:{label}:edge"),
                    space_id,
                    items[0][0],
                    items[1][0],
                ),
            )
    finally:
        connection.close()


def _wiki_identity(factory, *, space_id: str) -> dict[str, tuple[tuple[object, ...], ...]]:
    connection = factory.open()
    try:
        queries = {
            "items": """SELECT id,space_id,current_revision_id,row_version
                        FROM knowledge_item WHERE space_id=? ORDER BY id""",
            "revisions": """SELECT r.id,r.item_id,r.revision_no,r.parent_revision_id
                            FROM knowledge_revision AS r
                            JOIN knowledge_item AS i ON i.id=r.item_id
                            WHERE i.space_id=? ORDER BY r.item_id,r.revision_no""",
            "conversations": """SELECT id,space_id,captured_scope_epoch,row_version
                                FROM conversation WHERE space_id=? ORDER BY id""",
            "edges": """SELECT id,space_id,source_item_id,target_item_id,row_version
                        FROM knowledge_edge WHERE space_id=? ORDER BY id""",
        }
        return {
            name: tuple(tuple(row) for row in connection.execute(sql, (space_id,)))
            for name, sql in queries.items()
        }
    finally:
        connection.close()


@pytest.mark.scenario("WIKI-005")
def test_wiki_005_more_than_ten_create_with_exact_replay_and_no_owner_input(
    isolated_knowledge_layout,
) -> None:
    factory, _ = _migrate_current(isolated_knowledge_layout)
    ids = _seed(factory, ("owner", "user"), ("admin", "admin"))
    service = _service(factory)

    created = tuple(_create(service, index=index) for index in range(1, 12))
    assert len({result.resource_id for result in created}) == 11
    assert all(result.status == "created" and result.row_version == 1 for result in created)
    assert ids["owner"] not in {result.resource_id for result in created}

    connection = factory.open()
    try:
        profiles = PersonalWikiProfileRepository().list_by_owner(
            connection, owner_user_id="owner", limit=100
        )
        assert len(profiles) == 12
        assert [profile.space_id for profile in profiles if profile.is_default] == [
            ids["owner"]
        ]
        assert connection.execute(
            "SELECT count(*) FROM audit_event WHERE action='personal_wiki.create'"
        ).fetchone()[0] == 11
        assert connection.execute(
            "SELECT count(*) FROM outbox_event WHERE event_type='personal_wiki.created'"
        ).fetchone()[0] == 11
    finally:
        connection.close()

    before = _counts(factory)
    replay = service.create(
        CreatePersonalWikiCommand(
            actor_user_id="owner",
            actor_scope_epoch=1,
            operation_id=_uuid("create:owner:1"),
            request_id=_uuid("create-request:owner:1:replay"),
            display_name="Wiki 01",
            expected_row_version=1,
        )
    )
    assert replace(replay, request_id=created[0].request_id) == created[0]
    assert _counts(factory) == before

    collision = _create(service, index=20, display_name="ＡＢＣ")
    collision_before = _counts(factory)
    with pytest.raises(PersonalWikiServiceNameConflictError):
        _create(service, index=21, display_name="abc")
    assert _counts(factory) == collision_before
    assert collision.resource_id != ids["owner"]
    with pytest.raises(ValueError):
        _create(service, index=22, display_name="invalid\x00name")
    assert _counts(factory) == collision_before

    with pytest.raises(PersonalWikiServiceIdempotencyError):
        service.create(
            CreatePersonalWikiCommand(
                actor_user_id="owner",
                actor_scope_epoch=1,
                operation_id=_uuid("create:owner:1"),
                request_id=_uuid("create-request:owner:1:drift"),
                display_name="Changed input",
                expected_row_version=1,
            )
        )
    assert _counts(factory) == collision_before


@pytest.mark.scenario("WIKI-019")
def test_new_personal_wiki_write_enablement_is_explicit_and_flag_bounded(
    isolated_knowledge_layout,
) -> None:
    factory, _ = _migrate_current(isolated_knowledge_layout)
    _seed(factory, ("owner", "user"))

    conservative = _create(_service(factory), index=1, display_name="Read boundary")
    enabled_service = PersonalWikiLifecycleService(
        factory,
        cursor_secret=_CURSOR_SECRET,
        new_wiki_write_enabled=True,
    )
    writable = _create(enabled_service, index=2, display_name="Writable boundary")

    connection = factory.open(read_only=True)
    try:
        values = dict(
            connection.execute(
                "SELECT id,write_enabled FROM knowledge_space WHERE id IN (?,?)",
                (conservative.resource_id, writable.resource_id),
            ).fetchall()
        )
    finally:
        connection.close()
    assert values == {
        conservative.resource_id: 0,
        writable.resource_id: 1,
    }


@pytest.mark.scenario("WIKI-005", "WIKI-008", "WIKI-009", "WIKI-011")
def test_every_wiki_mutation_exact_replay_is_effect_free_and_drift_rejected(
    isolated_knowledge_layout,
) -> None:
    factory, _ = _migrate_current(isolated_knowledge_layout)
    ids = _seed(factory, ("owner", "user"))
    service = _service(factory)
    extra = _create(service, index=1, display_name="Replay target")

    rename_command = RenamePersonalWikiCommand(
        actor_user_id="owner",
        actor_scope_epoch=1,
        operation_id=_uuid("replay:rename"),
        request_id=_uuid("replay:rename:request"),
        space_id=extra.resource_id,
        display_name="Replay target renamed",
        expected_row_version=extra.row_version,
    )
    renamed = service.rename(rename_command)
    before = _counts(factory)
    replayed = service.rename(
        replace(rename_command, request_id=_uuid("replay:rename:request:2"))
    )
    assert replace(replayed, request_id=renamed.request_id) == renamed
    assert _counts(factory) == before
    with pytest.raises(PersonalWikiServiceIdempotencyError):
        service.rename(replace(rename_command, display_name="Drifted rename"))
    assert _counts(factory) == before

    default_command = SetDefaultPersonalWikiCommand(
        actor_user_id="owner",
        actor_scope_epoch=1,
        operation_id=_uuid("replay:default"),
        request_id=_uuid("replay:default:request"),
        space_id=extra.resource_id,
        expected_row_version=renamed.row_version,
    )
    selected = service.set_default(default_command)
    before = _counts(factory)
    replayed = service.set_default(
        replace(default_command, request_id=_uuid("replay:default:request:2"))
    )
    assert replace(replayed, request_id=selected.request_id) == selected
    assert _counts(factory) == before
    with pytest.raises(PersonalWikiServiceIdempotencyError):
        service.set_default(replace(default_command, expected_row_version=999))
    assert _counts(factory) == before

    connection = factory.open()
    try:
        old_default = PersonalWikiProfileRepository().require_owned(
            connection, owner_user_id="owner", space_id=ids["owner"]
        )
    finally:
        connection.close()
    returned = service.set_default(
        SetDefaultPersonalWikiCommand(
            actor_user_id="owner",
            actor_scope_epoch=1,
            operation_id=_uuid("replay:return-default"),
            request_id=_uuid("replay:return-default:request"),
            space_id=ids["owner"],
            expected_row_version=old_default.row_version,
        )
    )
    assert returned.status == "default_set"
    connection = factory.open()
    try:
        archive_target = PersonalWikiProfileRepository().require_owned(
            connection, owner_user_id="owner", space_id=extra.resource_id
        )
    finally:
        connection.close()

    archive_command = ArchivePersonalWikiCommand(
        actor_user_id="owner",
        actor_scope_epoch=1,
        operation_id=_uuid("replay:archive"),
        request_id=_uuid("replay:archive:request"),
        space_id=extra.resource_id,
        expected_row_version=archive_target.row_version,
        reason="Replay archive",
    )
    archived = service.archive(archive_command)
    before = _counts(factory)
    replayed = service.archive(
        replace(archive_command, request_id=_uuid("replay:archive:request:2"))
    )
    assert replace(replayed, request_id=archived.request_id) == archived
    assert _counts(factory) == before
    with pytest.raises(PersonalWikiServiceIdempotencyError):
        service.archive(replace(archive_command, reason="Drifted reason"))
    assert _counts(factory) == before

    restore_command = RestorePersonalWikiCommand(
        actor_user_id="owner",
        actor_scope_epoch=1,
        operation_id=_uuid("replay:restore"),
        request_id=_uuid("replay:restore:request"),
        space_id=extra.resource_id,
        expected_row_version=archived.row_version,
        make_default=False,
    )
    restored = service.restore(restore_command)
    before = _counts(factory)
    replayed = service.restore(
        replace(restore_command, request_id=_uuid("replay:restore:request:2"))
    )
    assert replace(replayed, request_id=restored.request_id) == restored
    assert _counts(factory) == before
    with pytest.raises(PersonalWikiServiceIdempotencyError):
        service.restore(replace(restore_command, make_default=True))
    assert _counts(factory) == before


@pytest.mark.scenario("WIKI-006")
def test_wiki_006_signed_filter_bound_active_and_archived_cursor_pages(
    isolated_knowledge_layout,
) -> None:
    factory, _ = _migrate_current(isolated_knowledge_layout)
    _seed(factory, ("owner", "user"), ("foreign", "user"))
    service = _service(factory)
    created = tuple(_create(service, index=index) for index in range(1, 13))

    first = service.list_profiles(
        ListPersonalWikisQuery(
            actor_user_id="owner",
            actor_scope_epoch=1,
            request_id=_uuid("list:active:1"),
            limit=5,
        )
    )
    assert len(first.items) == 5
    assert first.next_cursor is not None
    second = service.list_profiles(
        ListPersonalWikisQuery(
            actor_user_id="owner",
            actor_scope_epoch=1,
            request_id=_uuid("list:active:2"),
            cursor=first.next_cursor,
            limit=5,
        )
    )
    assert len(second.items) == 5
    assert not ({item.space_id for item in first.items} & {item.space_id for item in second.items})
    all_active = service.list_profiles(
        ListPersonalWikisQuery(
            actor_user_id="owner",
            actor_scope_epoch=1,
            request_id=_uuid("list:active:all"),
            limit=100,
        )
    )
    assert [(item.name_key, item.space_id) for item in all_active.items] == sorted(
        (item.name_key, item.space_id) for item in all_active.items
    )

    with pytest.raises(PersonalWikiCursorError):
        service.list_profiles(
            ListPersonalWikisQuery(
                actor_user_id="owner",
                actor_scope_epoch=1,
                request_id=_uuid("list:tampered"),
                cursor=first.next_cursor[:-1] + ("0" if first.next_cursor[-1] != "0" else "1"),
                limit=5,
            )
        )
    with pytest.raises(PersonalWikiCursorError):
        service.list_profiles(
            ListPersonalWikisQuery(
                actor_user_id="foreign",
                actor_scope_epoch=1,
                request_id=_uuid("list:wrong-owner"),
                cursor=first.next_cursor,
                limit=5,
            )
        )
    with pytest.raises(PersonalWikiCursorError):
        service.list_profiles(
            ListPersonalWikisQuery(
                actor_user_id="owner",
                actor_scope_epoch=1,
                request_id=_uuid("list:wrong-filter"),
                query="Wiki",
                cursor=first.next_cursor,
                limit=5,
            )
        )

    stale_page = service.list_profiles(
        ListPersonalWikisQuery(
            actor_user_id="owner",
            actor_scope_epoch=1,
            request_id=_uuid("list:stale-anchor:1"),
            query="Wiki 1",
            limit=2,
        )
    )
    assert stale_page.next_cursor is not None
    stale_anchor = stale_page.items[-1]
    service.rename(
        RenamePersonalWikiCommand(
            actor_user_id="owner",
            actor_scope_epoch=1,
            operation_id=_uuid("rename:stale-anchor"),
            request_id=_uuid("rename-request:stale-anchor"),
            space_id=stale_anchor.space_id,
            display_name="Cursor anchor changed",
            expected_row_version=stale_anchor.row_version,
        )
    )
    with pytest.raises(PersonalWikiCursorError, match="stale"):
        service.list_profiles(
            ListPersonalWikisQuery(
                actor_user_id="owner",
                actor_scope_epoch=1,
                request_id=_uuid("list:stale-anchor:2"),
                query="Wiki 1",
                cursor=stale_page.next_cursor,
                limit=2,
            )
        )

    for index, result in enumerate(created[:3], start=1):
        service.archive(
            ArchivePersonalWikiCommand(
                actor_user_id="owner",
                actor_scope_epoch=1,
                operation_id=_uuid(f"archive:{index}"),
                request_id=_uuid(f"archive-request:{index}"),
                space_id=result.resource_id,
                expected_row_version=result.row_version,
                reason="Archive management fixture",
            )
        )
    archived = service.list_profiles(
        ListPersonalWikisQuery(
            actor_user_id="owner",
            actor_scope_epoch=1,
            request_id=_uuid("list:archived"),
            lifecycle_status="archived",
            query="Wiki",
            limit=2,
        )
    )
    assert len(archived.items) == 2
    assert archived.next_cursor is not None
    assert all(item.lifecycle_status == "archived" for item in archived.items)
    assert [
        (-item.archived_at_ms, item.space_id)  # type: ignore[operator]
        for item in archived.items
    ] == sorted(
        (-item.archived_at_ms, item.space_id)  # type: ignore[operator]
        for item in archived.items
    )
    active_ids = {
        item.space_id
        for item in service.list_profiles(
            ListPersonalWikisQuery(
                actor_user_id="owner",
                actor_scope_epoch=1,
                request_id=_uuid("list:active:after-archive"),
                limit=100,
            )
        ).items
    }
    assert not active_ids & {result.resource_id for result in created[:3]}
    with pytest.raises(ValueError):
        service.list_profiles(
            ListPersonalWikisQuery(
                actor_user_id="owner",
                actor_scope_epoch=1,
                request_id=_uuid("list:unbounded"),
                limit=101,
            )
        )


@pytest.mark.scenario("WIKI-007", "WIKI-008", "WIKI-009", "WIKI-010", "WIKI-011")
def test_wiki_007_through_011_owner_cas_default_archive_restore_identity(
    isolated_knowledge_layout,
) -> None:
    factory, _ = _migrate_current(isolated_knowledge_layout)
    ids = _seed(
        factory,
        ("owner", "user"),
        ("foreign", "user"),
        ("admin", "admin"),
    )
    service = _service(factory)
    extra = _create(service, index=1, display_name="Project Alpha")
    _seed_wiki_identity_fixture(factory, space_id=extra.resource_id, label="rename")
    rename_identity_before = _wiki_identity(factory, space_id=extra.resource_id)

    before_foreign = _counts(factory)
    for actor in ("foreign", "admin"):
        with pytest.raises(PersonalWikiConcealedError):
            service.rename(
                RenamePersonalWikiCommand(
                    actor_user_id=actor,
                    actor_scope_epoch=1,
                    operation_id=_uuid(f"foreign-rename:{actor}"),
                    request_id=_uuid(f"foreign-rename-request:{actor}"),
                    space_id=extra.resource_id,
                    display_name="Forbidden rename",
                    expected_row_version=1,
                )
            )
        with pytest.raises(PersonalWikiConcealedError):
            service.set_default(
                SetDefaultPersonalWikiCommand(
                    actor_user_id=actor,
                    actor_scope_epoch=1,
                    operation_id=_uuid(f"foreign-default:{actor}"),
                    request_id=_uuid(f"foreign-default-request:{actor}"),
                    space_id=extra.resource_id,
                    expected_row_version=1,
                )
            )
        with pytest.raises(PersonalWikiConcealedError):
            service.archive(
                ArchivePersonalWikiCommand(
                    actor_user_id=actor,
                    actor_scope_epoch=1,
                    operation_id=_uuid(f"foreign-archive:{actor}"),
                    request_id=_uuid(f"foreign-archive-request:{actor}"),
                    space_id=extra.resource_id,
                    expected_row_version=1,
                )
            )
    assert _counts(factory) == before_foreign

    renamed = service.rename(
        RenamePersonalWikiCommand(
            actor_user_id="owner",
            actor_scope_epoch=1,
            operation_id=_uuid("rename:owner"),
            request_id=_uuid("rename-request:owner"),
            space_id=extra.resource_id,
            display_name="프로젝트 알파",
            expected_row_version=1,
        )
    )
    assert renamed.status == "renamed" and renamed.row_version == 2
    assert _wiki_identity(factory, space_id=extra.resource_id) == rename_identity_before
    with pytest.raises(PersonalWikiServiceVersionError):
        service.set_default(
            SetDefaultPersonalWikiCommand(
                actor_user_id="owner",
                actor_scope_epoch=1,
                operation_id=_uuid("default:stale"),
                request_id=_uuid("default-request:stale"),
                space_id=extra.resource_id,
                expected_row_version=1,
            )
        )

    selected = service.set_default(
        SetDefaultPersonalWikiCommand(
            actor_user_id="owner",
            actor_scope_epoch=1,
            operation_id=_uuid("default:owner"),
            request_id=_uuid("default-request:owner"),
            space_id=extra.resource_id,
            expected_row_version=renamed.row_version,
        )
    )
    assert selected.status == "default_set" and selected.row_version == 3

    with pytest.raises(PersonalWikiServiceDefaultRequiredError):
        service.archive(
            ArchivePersonalWikiCommand(
                actor_user_id="owner",
                actor_scope_epoch=1,
                operation_id=_uuid("archive:current-default"),
                request_id=_uuid("archive-request:current-default"),
                space_id=extra.resource_id,
                expected_row_version=selected.row_version,
            )
        )

    _seed_wiki_identity_fixture(factory, space_id=ids["owner"], label="archive")
    connection = factory.open()
    try:
        old_default = PersonalWikiProfileRepository().require_owned(
            connection, owner_user_id="owner", space_id=ids["owner"]
        )
    finally:
        connection.close()
    identity_before = _wiki_identity(factory, space_id=ids["owner"])
    assert {name: len(rows) for name, rows in identity_before.items()} == {
        "items": 2,
        "revisions": 2,
        "conversations": 1,
        "edges": 1,
    }

    archived = service.archive(
        ArchivePersonalWikiCommand(
            actor_user_id="owner",
            actor_scope_epoch=1,
            operation_id=_uuid("archive:old-default"),
            request_id=_uuid("archive-request:old-default"),
            space_id=ids["owner"],
            expected_row_version=old_default.row_version,
            reason="Owner archive test",
        )
    )
    assert archived.status == "archived"
    assert archived.resource_id == ids["owner"]
    assert archived.lifecycle_generation == 2

    before_foreign_restore = _counts(factory)
    for actor in ("foreign", "admin"):
        with pytest.raises(PersonalWikiConcealedError):
            service.restore(
                RestorePersonalWikiCommand(
                    actor_user_id=actor,
                    actor_scope_epoch=1,
                    operation_id=_uuid(f"foreign-restore:{actor}"),
                    request_id=_uuid(f"foreign-restore-request:{actor}"),
                    space_id=ids["owner"],
                    expected_row_version=archived.row_version,
                )
            )
    assert _counts(factory) == before_foreign_restore

    restored = service.restore(
        RestorePersonalWikiCommand(
            actor_user_id="owner",
            actor_scope_epoch=1,
            operation_id=_uuid("restore:old-default"),
            request_id=_uuid("restore-request:old-default"),
            space_id=ids["owner"],
            expected_row_version=archived.row_version,
            make_default=False,
        )
    )
    assert restored.status == "restored"
    assert restored.resource_id == ids["owner"]
    assert restored.lifecycle_generation == 3

    connection = factory.open()
    try:
        profile = PersonalWikiProfileRepository().require_owned(
            connection, owner_user_id="owner", space_id=ids["owner"]
        )
        assert profile.lifecycle_status == "active"
        assert profile.is_default is False
        assert profile.archive_reason is None
        assert PersonalWikiProfileRepository().get_default(connection, "owner").space_id == extra.resource_id  # type: ignore[union-attr]
        restore_event = connection.execute(
            """SELECT payload_json FROM outbox_event
               WHERE event_type='personal_wiki.restored' AND space_id=?""",
            (ids["owner"],),
        ).fetchone()
        payload = json.loads(restore_event[0])
        assert payload["lifecycle_generation"] == 3
        assert payload["space_id"] == ids["owner"]
        assert isinstance(payload["audit_event_id"], int)
    finally:
        connection.close()
    identity_after = _wiki_identity(factory, space_id=ids["owner"])
    assert identity_after == identity_before

    archived_again = service.archive(
        ArchivePersonalWikiCommand(
            actor_user_id="owner",
            actor_scope_epoch=1,
            operation_id=_uuid("archive:old-default:again"),
            request_id=_uuid("archive-request:old-default:again"),
            space_id=ids["owner"],
            expected_row_version=restored.row_version,
            reason="Atomic restore-default test",
        )
    )
    restored_default = service.restore(
        RestorePersonalWikiCommand(
            actor_user_id="owner",
            actor_scope_epoch=1,
            operation_id=_uuid("restore:old-default:as-default"),
            request_id=_uuid("restore-request:old-default:as-default"),
            space_id=ids["owner"],
            expected_row_version=archived_again.row_version,
            make_default=True,
        )
    )
    connection = factory.open()
    try:
        assert restored_default.lifecycle_generation == 5
        assert PersonalWikiProfileRepository().get_default(connection, "owner").space_id == ids["owner"]  # type: ignore[union-attr]
        defaults = connection.execute(
            """SELECT count(*) FROM personal_wiki_profile
               WHERE owner_user_id='owner' AND lifecycle_status='active' AND is_default=1"""
        ).fetchone()[0]
        assert defaults == 1
    finally:
        connection.close()


@pytest.mark.scenario("WIKI-012")
def test_wiki_012_lifecycle_service_has_no_scheduler_or_age_archive_surface() -> None:
    source = (
        Path(__file__).resolve().parents[3]
        / "knowledge_core"
        / "services"
        / "personal_wikis.py"
    ).read_text(encoding="utf-8")
    assert "auto_archive" not in source
    assert "archive_after" not in source
    assert "scheduler" not in {
        name
        for name in PersonalWikiLifecycleService.__dict__
        if not name.startswith("__")
    }


@pytest.mark.scenario("WIKI-005", "WIKI-008", "WIKI-011")
def test_wiki_mutation_failpoints_roll_back_domain_audit_outbox_and_dedup(
    isolated_knowledge_layout,
) -> None:
    factory, _ = _migrate_current(isolated_knowledge_layout)
    _seed(factory, ("owner", "user"))
    phases = (
        "personal_wiki.after_domain",
        "personal_wiki.after_audit",
        "personal_wiki.after_outbox",
        "personal_wiki.before_commit",
    )
    for index, phase in enumerate(phases, start=1):
        baseline = _counts(factory)

        def failpoint(name: str, expected: str = phase) -> None:
            if name == expected:
                raise RuntimeError(expected)

        failed_service = PersonalWikiLifecycleService(
            factory,
            cursor_secret=_CURSOR_SECRET,
            failpoint=failpoint,
        )
        command = CreatePersonalWikiCommand(
            actor_user_id="owner",
            actor_scope_epoch=1,
            operation_id=_uuid(f"failpoint-operation:{index}"),
            request_id=_uuid(f"failpoint-request:{index}"),
            display_name=f"Failpoint Wiki {index}",
            expected_row_version=1,
        )
        with pytest.raises(PersonalWikiFailpointTriggered):
            failed_service.create(command)
        assert _counts(factory) == baseline

        retried = _service(factory).create(
            replace(command, request_id=_uuid(f"failpoint-retry:{index}"))
        )
        assert retried.status == "created"
        after = _counts(factory)
        assert after["knowledge_space"] == baseline["knowledge_space"] + 1
        assert after["personal_wiki_profile"] == baseline["personal_wiki_profile"] + 1
        assert after["audit_event"] == baseline["audit_event"] + 1
        assert after["outbox_event"] == baseline["outbox_event"] + 1


@pytest.mark.scenario("WIKI-005", "WIKI-006")
def test_sbe_2_1_agent_runtime_requires_and_binds_explicit_cursor_secret(
    isolated_knowledge_layout,
) -> None:
    factory, _ = _migrate_current(isolated_knowledge_layout)
    flags = FeatureFlags(
        core_enabled=True,
        multi_wiki_schema=True,
        multi_wiki_reads=True,
        personal_reads=True,
    )
    with pytest.raises(ValueError, match="cursor signing secret"):
        compose_canonical_agent_runtime(
            factory,
            flags=flags,
            principal_resolver=lambda: None,
        )
    runtime = compose_canonical_agent_runtime(
        factory,
        flags=flags,
        principal_resolver=lambda: None,
        personal_wiki_cursor_secret=_CURSOR_SECRET,
    )
    assert isinstance(runtime.services.personal_wikis, PersonalWikiLifecycleService)

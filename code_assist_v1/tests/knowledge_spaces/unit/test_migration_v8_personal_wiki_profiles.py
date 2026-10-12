from __future__ import annotations

import hashlib
import sqlite3
from uuid import UUID, uuid5

import pytest

from code_assist_v1.knowledge_core.db import ConnectionFactory, transaction
from code_assist_v1.knowledge_core.errors import (
    KnowledgeCoreMigrationError,
    KnowledgeCoreSchemaMismatchError,
)
from code_assist_v1.knowledge_core.migrations import (
    CANONICAL_MIGRATION_CATALOG,
    MigrationCatalog,
    MigrationStorageLayout,
    SCHEMA_V1_MIGRATION,
    SCHEMA_V2_MIGRATION,
    SCHEMA_V3_MIGRATION,
    SCHEMA_V4_MIGRATION,
    SCHEMA_V5_MIGRATION,
    SCHEMA_V6_MIGRATION,
    SCHEMA_V7_MIGRATION,
    SCHEMA_V8_MIGRATION,
    SingleMigrator,
    V008_EXPECTED_SCHEMA_SHA256,
    schema_sha256,
    verify_canonical_integrity,
)
from code_assist_v1.knowledge_core.repositories.personal_wikis import (
    DEFAULT_PERSONAL_WIKI_DISPLAY_NAME,
    PersonalWikiNameConflictError,
    PersonalWikiProfileRepository,
    PersonalWikiRepositoryError,
    normalize_personal_wiki_name,
)
from code_assist_v1.knowledge_core.repositories.spaces import SpaceRepository
from code_assist_v1.knowledge_core.repositories.users import (
    LegacyUserRecord,
    UserRepository,
)
from code_assist_v1.test_safety import create_isolated_test_layout


pytestmark = pytest.mark.unit

_NAMESPACE = UUID("f62e7143-7cce-43c4-ae31-e980dd04bf6b")
_V7_CATALOG = MigrationCatalog(
    (
        SCHEMA_V1_MIGRATION,
        SCHEMA_V2_MIGRATION,
        SCHEMA_V3_MIGRATION,
        SCHEMA_V4_MIGRATION,
        SCHEMA_V5_MIGRATION,
        SCHEMA_V6_MIGRATION,
        SCHEMA_V7_MIGRATION,
    )
)
_V8_CATALOG = MigrationCatalog((*_V7_CATALOG.migrations, SCHEMA_V8_MIGRATION))


def _uuid(label: str) -> str:
    return str(uuid5(_NAMESPACE, label))


def _layout(value) -> MigrationStorageLayout:
    return MigrationStorageLayout(
        data_root=value.root,
        database=value.core_db_path,
        blob_root=value.blobs_dir,
        projection_root=value.projections_dir,
    )


def _factory(value, catalog=_V8_CATALOG) -> ConnectionFactory:
    return ConnectionFactory._for_isolated_tests(value.core_db_path, catalog)


def _migrate(value, catalog=_V8_CATALOG):
    factory = _factory(value, catalog)
    result = SingleMigrator(
        factory,
        app_version="sbe-1.1-v8-test",
        storage_layout=_layout(value),
    ).run()
    return factory, result


def _user(user_id: str) -> LegacyUserRecord:
    return LegacyUserRecord(
        id=user_id,
        display_name=user_id.title(),
        password_hash=f"scrypt:sbe-1.1:{user_id}",
        system_role="user",
        account_status="active",
        created_at_ms=100,
        updated_at_ms=100,
    )


def _seed_v7_owner_space(factory: ConnectionFactory, *, user_id: str = "owner") -> str:
    space_id = _uuid(f"space:{user_id}:default")
    connection = factory.open()
    try:
        with transaction(connection):
            UserRepository().import_legacy(connection, _user(user_id))
            SpaceRepository().ensure_personal(
                connection,
                space_id=space_id,
                user_id=user_id,
                created_at_ms=150,
            )
    finally:
        connection.close()
    return space_id


def _seed_v7_content(factory: ConnectionFactory, *, space_id: str) -> dict[str, object]:
    item_id = _uuid("v7:item")
    revision_id = _uuid("v7:revision")
    conversation_id = _uuid("v7:conversation")
    body = "# Existing v7 knowledge\n\nThe identity and body must survive v8.\n"
    body_bytes = body.encode("utf-8")
    body_hash = hashlib.sha256(body_bytes).hexdigest()
    connection = factory.open()
    try:
        with transaction(connection):
            connection.execute(
                """INSERT INTO knowledge_item(
                     id,space_id,lineage_group_id,lifecycle_status,curation_status,
                     current_revision_id,last_curation_transition_id,
                     latest_deactivation_episode_id,redirect_item_id,created_by,
                     updated_by,created_at,updated_at,row_version
                   ) VALUES (?,?,NULL,'active','provisional',?,NULL,NULL,NULL,
                             'owner','owner',200,200,1)""",
                (item_id, space_id, revision_id),
            )
            connection.execute(
                """INSERT INTO knowledge_revision(
                     id,item_id,revision_no,parent_revision_id,base_revision_id,
                     title,raw_content_sha256,normalized_content_sha256,
                     content_size_bytes,normalizer_version,metadata_json,
                     change_kind,change_reason,created_by,created_at
                   ) VALUES (?,?,1,NULL,NULL,?,?,?,?,?,'{}','initial',
                             'v7 migration preservation fixture','owner',200)""",
                (
                    revision_id,
                    item_id,
                    "Existing v7 knowledge",
                    body_hash,
                    body_hash,
                    len(body_bytes),
                    "markdown_lf_v1",
                ),
            )
            connection.execute(
                """INSERT INTO knowledge_revision_body(
                     revision_id,item_id,content_md,content_blob_hash,
                     content_size_bytes,content_sha256,created_at
                   ) VALUES (?,?,?,NULL,?,?,200)""",
                (revision_id, item_id, body, len(body_bytes), body_hash),
            )
            connection.execute(
                """INSERT INTO conversation(
                     id,owner_user_id,space_id,captured_scope_epoch,title,status,
                     metadata_json,created_at,updated_at,row_version
                   ) VALUES (?,'owner',?,1,'Existing session','active','{}',210,210,1)""",
                (conversation_id, space_id),
            )
        return {
            "space": tuple(
                connection.execute(
                    "SELECT id,owner_user_id,created_at,updated_at FROM knowledge_space WHERE id=?",
                    (space_id,),
                ).fetchone()
            ),
            "item": tuple(
                connection.execute(
                    "SELECT id,space_id,current_revision_id FROM knowledge_item WHERE id=?",
                    (item_id,),
                ).fetchone()
            ),
            "revision": tuple(
                connection.execute(
                    """SELECT id,item_id,raw_content_sha256,
                              normalized_content_sha256 FROM knowledge_revision
                       WHERE id=?""",
                    (revision_id,),
                ).fetchone()
            ),
            "body": tuple(
                connection.execute(
                    "SELECT revision_id,content_sha256,content_md FROM knowledge_revision_body WHERE revision_id=?",
                    (revision_id,),
                ).fetchone()
            ),
            "conversation": tuple(
                connection.execute(
                    "SELECT id,owner_user_id,space_id FROM conversation WHERE id=?",
                    (conversation_id,),
                ).fetchone()
            ),
        }
    finally:
        connection.close()


@pytest.mark.scenario("WIKI-001")
@pytest.mark.scenario("WIKI-002")
def test_v7_upgrade_preserves_identity_and_body_and_matches_pristine_fingerprint(
    isolated_knowledge_layout,
    tmp_path,
) -> None:
    v7_factory, first = _migrate(isolated_knowledge_layout, _V7_CATALOG)
    assert first.applied_versions == (1, 2, 3, 4, 5, 6, 7)
    space_id = _seed_v7_owner_space(v7_factory)
    before = _seed_v7_content(v7_factory, space_id=space_id)

    upgraded_factory, upgraded = _migrate(isolated_knowledge_layout)
    assert upgraded.applied_versions == (8,)
    assert upgraded.schema_version == 8
    connection = upgraded_factory.open()
    try:
        assert schema_sha256(connection) == V008_EXPECTED_SCHEMA_SHA256
        assert before == {
            "space": tuple(
                connection.execute(
                    "SELECT id,owner_user_id,created_at,updated_at FROM knowledge_space WHERE id=?",
                    (space_id,),
                ).fetchone()
            ),
            "item": tuple(
                connection.execute(
                    "SELECT id,space_id,current_revision_id FROM knowledge_item"
                ).fetchone()
            ),
            "revision": tuple(
                connection.execute(
                    """SELECT id,item_id,raw_content_sha256,
                              normalized_content_sha256 FROM knowledge_revision"""
                ).fetchone()
            ),
            "body": tuple(
                connection.execute(
                    "SELECT revision_id,content_sha256,content_md FROM knowledge_revision_body"
                ).fetchone()
            ),
            "conversation": tuple(
                connection.execute(
                    "SELECT id,owner_user_id,space_id FROM conversation"
                ).fetchone()
            ),
        }
        profile = PersonalWikiProfileRepository().get_default(connection, "owner")
        assert profile is not None
        assert profile.space_id == space_id
        assert profile.display_name == DEFAULT_PERSONAL_WIKI_DISPLAY_NAME
        assert profile.name_key == normalize_personal_wiki_name(
            DEFAULT_PERSONAL_WIKI_DISPLAY_NAME
        )[1]
        assert profile.lifecycle_status == "active"
        assert profile.lifecycle_generation == 1
        assert profile.is_default is True
    finally:
        connection.close()

    pristine_layout = create_isolated_test_layout(
        tmp_path / "pristine-v8", user_id="pristine-owner"
    )
    pristine_factory, pristine = _migrate(pristine_layout)
    assert pristine.applied_versions == (1, 2, 3, 4, 5, 6, 7, 8)
    pristine_connection = pristine_factory.open()
    try:
        assert schema_sha256(pristine_connection) == V008_EXPECTED_SCHEMA_SHA256
    finally:
        pristine_connection.close()

    repeated_factory, repeated = _migrate(isolated_knowledge_layout)
    assert repeated.applied_versions == ()
    assert repeated.schema_version == 8
    del repeated_factory


@pytest.mark.scenario("WIKI-002")
def test_invalid_v7_input_rolls_back_the_whole_v8_upgrade(
    isolated_knowledge_layout,
) -> None:
    v7_factory, _ = _migrate(isolated_knowledge_layout, _V7_CATALOG)
    connection = v7_factory.open()
    try:
        with transaction(connection):
            UserRepository().import_legacy(connection, _user("orphan"))
    finally:
        connection.close()

    with pytest.raises(KnowledgeCoreMigrationError):
        _migrate(isolated_knowledge_layout)

    raw = sqlite3.connect(isolated_knowledge_layout.core_db_path)
    try:
        assert raw.execute("PRAGMA user_version").fetchone() == (7,)
        assert raw.execute(
            "SELECT count(*) FROM schema_migration WHERE version=8"
        ).fetchone() == (0,)
        assert raw.execute(
            "SELECT count(*) FROM sqlite_schema WHERE name='personal_wiki_profile'"
        ).fetchone() == (0,)
        assert raw.execute(
            "SELECT count(*) FROM sqlite_schema WHERE name='ux_space_personal_owner'"
        ).fetchone() == (1,)
    finally:
        raw.close()


@pytest.mark.scenario("WIKI-003")
def test_v8_has_exact_indexes_no_count_cap_and_uses_stable_cursor_plan(
    isolated_knowledge_layout,
) -> None:
    factory, _ = _migrate(isolated_knowledge_layout)
    repository = PersonalWikiProfileRepository()
    spaces = SpaceRepository()
    connection = factory.open()
    try:
        with transaction(connection):
            UserRepository().import_legacy(connection, _user("owner"))
            default_id = _uuid("unlimited:default")
            spaces.ensure_personal(
                connection,
                space_id=default_id,
                user_id="owner",
                created_at_ms=1000,
            )
            for index in range(12):
                space_id = _uuid(f"unlimited:{index}")
                connection.execute(
                    """INSERT INTO knowledge_space(
                         id,space_kind,owner_user_id,owner_org_unit_id,status,
                         write_enabled,generation,created_at,updated_at,row_version
                       ) VALUES (?,'personal','owner',NULL,'active',0,0,?,?,1)""",
                    (space_id, 1100 + index, 1100 + index),
                )
                repository.insert_initial_profile(
                    connection,
                    space_id=space_id,
                    owner_user_id="owner",
                    display_name=f"Wiki {index:02d}",
                    is_default=False,
                    created_at_ms=1100 + index,
                )

        assert len(repository.list_by_owner(connection, owner_user_id="owner", limit=100)) == 13
        assert spaces.get_personal(connection, "owner").id == default_id
        with transaction(connection):
            compatibility = spaces.ensure_personal(
                connection,
                space_id=_uuid("legacy-compatible-candidate"),
                user_id="owner",
                created_at_ms=2000,
            )
        assert compatibility.created is False
        assert compatibility.space.id == default_id

        object_names = {
            row[0]
            for row in connection.execute(
                    """SELECT name FROM sqlite_schema WHERE type='index'
                       AND name LIKE '%personal_wiki%'
                       AND name NOT LIKE 'sqlite_%'"""
            ).fetchall()
        }
        assert object_names == {
            "ix_personal_wiki_active_name_cursor",
            "ix_personal_wiki_active_recent_cursor",
            "ix_personal_wiki_archived_cursor",
            "ux_personal_wiki_active_default",
            "ux_personal_wiki_owner_name_key",
        }
        assert connection.execute(
            "SELECT count(*) FROM sqlite_schema WHERE name='ux_space_personal_owner'"
        ).fetchone()[0] == 0
        schema_text = "\n".join(
            row[0]
            for row in connection.execute(
                """SELECT sql FROM sqlite_schema
                   WHERE name='personal_wiki_profile'
                      OR name LIKE '%personal_wiki%'"""
            ).fetchall()
            if row[0] is not None
        ).casefold()
        assert "wiki_count" not in schema_text
        assert "max_wiki" not in schema_text

        plan = " ".join(
            str(value)
            for row in connection.execute(
                """EXPLAIN QUERY PLAN
                   SELECT space_id FROM personal_wiki_profile
                   WHERE owner_user_id=? AND lifecycle_status='active'
                     AND (name_key,space_id)>(?,?)
                   ORDER BY name_key,space_id LIMIT ?""",
                ("owner", "wiki 00", _uuid("unlimited:0"), 20),
            ).fetchall()
            for value in row
        )
        assert "ix_personal_wiki_active_name_cursor" in plan
    finally:
        connection.close()


@pytest.mark.scenario("WIKI-004")
def test_v8_invalid_profile_name_default_and_lifecycle_states_fail_closed(
    isolated_knowledge_layout,
) -> None:
    factory, _ = _migrate(isolated_knowledge_layout)
    repository = PersonalWikiProfileRepository()
    connection = factory.open()
    try:
        with transaction(connection):
            UserRepository().import_legacy_many(connection, (_user("owner"), _user("other")))
            default_id = _uuid("invalid:default")
            SpaceRepository().ensure_personal(
                connection,
                space_id=default_id,
                user_id="owner",
                created_at_ms=1000,
            )
            second_id = _uuid("invalid:second")
            connection.execute(
                """INSERT INTO knowledge_space(
                     id,space_kind,owner_user_id,owner_org_unit_id,status,
                     write_enabled,generation,created_at,updated_at,row_version
                   ) VALUES (?,'personal','owner',NULL,'active',0,0,1001,1001,1)""",
                (second_id,),
            )
            repository.insert_initial_profile(
                connection,
                space_id=second_id,
                owner_user_id="owner",
                display_name="Ｋnowledge",
                is_default=False,
                created_at_ms=1001,
            )

        with pytest.raises(PersonalWikiNameConflictError):
            with transaction(connection):
                duplicate_id = _uuid("invalid:duplicate")
                connection.execute(
                    """INSERT INTO knowledge_space(
                         id,space_kind,owner_user_id,owner_org_unit_id,status,
                         write_enabled,generation,created_at,updated_at,row_version
                       ) VALUES (?,'personal','owner',NULL,'active',0,0,1002,1002,1)""",
                    (duplicate_id,),
                )
                repository.insert_initial_profile(
                    connection,
                    space_id=duplicate_id,
                    owner_user_id="owner",
                    display_name="knowledge",
                    is_default=False,
                    created_at_ms=1002,
                )

        with pytest.raises(sqlite3.IntegrityError, match="PERSONAL_WIKI_OWNER_MISMATCH"):
            with transaction(connection):
                connection.execute(
                    """INSERT INTO personal_wiki_profile(
                         space_id,owner_user_id,display_name,name_key,
                         lifecycle_status,lifecycle_generation,is_default,
                         created_at,updated_at,row_version
                       ) VALUES (?,'other','Mismatch','mismatch','active',1,0,1001,1001,1)""",
                    (_uuid("invalid:unowned"),),
                )

        with pytest.raises(sqlite3.IntegrityError):
            with transaction(connection):
                connection.execute(
                    """UPDATE personal_wiki_profile
                       SET lifecycle_status='archived',lifecycle_generation=2,
                           archived_at=1100,archived_by_user_id='owner'
                       WHERE space_id=?""",
                    (default_id,),
                )
        with pytest.raises(
            sqlite3.IntegrityError,
            match="PERSONAL_WIKI_LIFECYCLE_GENERATION_INVALID",
        ):
            with transaction(connection):
                connection.execute(
                    """UPDATE personal_wiki_profile
                       SET lifecycle_status='archived',lifecycle_generation=3,
                           archived_at=1100,archived_by_user_id='owner'
                       WHERE space_id=?""",
                    (second_id,),
                )
        with pytest.raises(
            sqlite3.IntegrityError,
            match="PERSONAL_WIKI_LIFECYCLE_GENERATION_INVALID",
        ):
            with transaction(connection):
                connection.execute(
                    """UPDATE personal_wiki_profile
                       SET lifecycle_generation=2 WHERE space_id=?""",
                    (second_id,),
                )
        with pytest.raises(sqlite3.IntegrityError, match="PERSONAL_WIKI_PROFILE_IMMUTABLE"):
            with transaction(connection):
                connection.execute(
                    "DELETE FROM personal_wiki_profile WHERE space_id=?",
                    (second_id,),
                )

        with pytest.raises(PersonalWikiRepositoryError):
            with transaction(connection):
                drift_id = _uuid("invalid:drift")
                connection.execute(
                    """INSERT INTO knowledge_space(
                         id,space_kind,owner_user_id,owner_org_unit_id,status,
                         write_enabled,generation,created_at,updated_at,row_version
                       ) VALUES (?,'personal','owner',NULL,'active',0,0,1003,1003,1)""",
                    (drift_id,),
                )
                connection.execute(
                    """INSERT INTO personal_wiki_profile(
                         space_id,owner_user_id,display_name,name_key,
                         lifecycle_status,lifecycle_generation,is_default,
                         created_at,updated_at,row_version
                       ) VALUES (?,'owner','Drift','wrong-key','active',1,0,1003,1003,1)""",
                    (drift_id,),
                )
                repository.get_by_space_id(connection, drift_id)

        with pytest.raises(KnowledgeCoreMigrationError):
            with transaction(connection):
                connection.execute(
                    "UPDATE personal_wiki_profile SET is_default=0 WHERE space_id=?",
                    (default_id,),
                )
                verify_canonical_integrity(connection, _V8_CATALOG)
    finally:
        connection.close()


@pytest.mark.scenario("WIKI-004")
def test_unknown_schema_object_and_tampered_ledger_block_v8_upgrade(
    isolated_knowledge_layout,
) -> None:
    v7_factory, _ = _migrate(isolated_knowledge_layout, _V7_CATALOG)
    _seed_v7_owner_space(v7_factory)
    raw = sqlite3.connect(isolated_knowledge_layout.core_db_path)
    try:
        raw.execute("CREATE TABLE unexpected_v8_probe(id INTEGER PRIMARY KEY) STRICT")
        raw.commit()
    finally:
        raw.close()
    with pytest.raises((KnowledgeCoreMigrationError, KnowledgeCoreSchemaMismatchError)):
        _migrate(isolated_knowledge_layout)

    raw = sqlite3.connect(isolated_knowledge_layout.core_db_path)
    try:
        raw.execute("DROP TABLE unexpected_v8_probe")
        raw.execute(
            "UPDATE schema_migration SET checksum_sha256=? WHERE version=7",
            ("0" * 64,),
        )
        raw.commit()
    finally:
        raw.close()
    with pytest.raises((KnowledgeCoreMigrationError, KnowledgeCoreSchemaMismatchError)):
        _migrate(isolated_knowledge_layout)

from __future__ import annotations

import multiprocessing
from pathlib import Path
from queue import Empty
from uuid import UUID, uuid5

import pytest

from code_assist_v1.knowledge_core.db import ConnectionFactory, transaction
from code_assist_v1.knowledge_core.migrations import CANONICAL_MIGRATION_CATALOG
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
    PersonalWikiLifecycleService,
    RestorePersonalWikiCommand,
    SetDefaultPersonalWikiCommand,
)
from code_assist_v1.tests.knowledge_spaces.unit.test_migration_v10_semantic_graph_contracts import (
    _migrate_current,
)


pytestmark = pytest.mark.integration

_NAMESPACE = UUID("147e60f4-10bc-4dcb-8039-72471e86e56b")
_CURSOR_SECRET = b"sbe-2.1-multiprocess-cursor-secret!!"


def _uuid(label: str) -> str:
    return str(uuid5(_NAMESPACE, label))


def _factory(database: str) -> ConnectionFactory:
    return ConnectionFactory._for_isolated_tests(
        Path(database), CANONICAL_MIGRATION_CATALOG
    )


def _race_worker(
    database: str,
    action: str,
    target_space_id: str,
    expected_row_version: int,
    index: int,
    barrier,
    results,
) -> None:
    service = PersonalWikiLifecycleService(
        _factory(database), cursor_secret=_CURSOR_SECRET
    )
    operation_id = _uuid(f"{action}:operation:{index}")
    request_id = _uuid(f"{action}:request:{index}")
    barrier.wait(timeout=30)
    try:
        if action == "set_default":
            result = service.set_default(
                SetDefaultPersonalWikiCommand(
                    actor_user_id="owner",
                    actor_scope_epoch=1,
                    operation_id=operation_id,
                    request_id=request_id,
                    space_id=target_space_id,
                    expected_row_version=expected_row_version,
                )
            )
        elif action == "archive":
            result = service.archive(
                ArchivePersonalWikiCommand(
                    actor_user_id="owner",
                    actor_scope_epoch=1,
                    operation_id=operation_id,
                    request_id=request_id,
                    space_id=target_space_id,
                    expected_row_version=expected_row_version,
                    reason="multiprocess race",
                )
            )
        elif action == "restore":
            result = service.restore(
                RestorePersonalWikiCommand(
                    actor_user_id="owner",
                    actor_scope_epoch=1,
                    operation_id=operation_id,
                    request_id=request_id,
                    space_id=target_space_id,
                    expected_row_version=expected_row_version,
                )
            )
        else:  # pragma: no cover - parent supplies the closed action set
            raise ValueError("unknown race action")
        results.put(("ok", result.status, result.row_version, result.lifecycle_generation))
    except Exception as exc:
        results.put(
            (
                "error",
                getattr(exc, "code", type(exc).__name__),
                str(exc),
            )
        )


def _race(database: Path, action: str, target: str, version: int, count: int = 8):
    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(count)
    results = context.Queue()
    processes = [
        context.Process(
            target=_race_worker,
            args=(str(database), action, target, version, index, barrier, results),
        )
        for index in range(count)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=60)
        assert not process.is_alive(), f"race child {process.pid} did not exit"
        assert process.exitcode == 0
    values = []
    for _ in processes:
        try:
            values.append(results.get(timeout=5))
        except Empty as exc:  # pragma: no cover - process exit assertions diagnose this
            raise AssertionError("race child returned no result") from exc
    return tuple(values)


@pytest.mark.scenario("WIKI-008", "WIKI-009", "WIKI-011")
def test_wiki_lifecycle_real_process_races_have_one_cas_winner(
    isolated_knowledge_layout,
) -> None:
    factory, _ = _migrate_current(
        isolated_knowledge_layout,
        catalog=CANONICAL_MIGRATION_CATALOG,
    )
    connection = factory.open()
    try:
        with transaction(connection):
            UserRepository().import_legacy(
                connection,
                LegacyUserRecord(
                    id="owner",
                    display_name="Owner",
                    password_hash="scrypt:sbe-2.1:multiprocess",
                    system_role="user",
                    account_status="active",
                    created_at_ms=100,
                    updated_at_ms=100,
                ),
            )
            default_id = _uuid("default")
            SpaceRepository().ensure_personal(
                connection,
                space_id=default_id,
                user_id="owner",
                created_at_ms=200,
            )
    finally:
        connection.close()

    service = PersonalWikiLifecycleService(factory, cursor_secret=_CURSOR_SECRET)
    extra = service.create(
        CreatePersonalWikiCommand(
            actor_user_id="owner",
            actor_scope_epoch=1,
            operation_id=_uuid("create"),
            request_id=_uuid("create-request"),
            display_name="Race target",
            expected_row_version=1,
        )
    )

    default_race = _race(
        isolated_knowledge_layout.core_db_path,
        "set_default",
        extra.resource_id,
        extra.row_version,
    )
    assert sum(value[0] == "ok" for value in default_race) == 1, default_race
    assert all(
        value[0] == "ok" or value[1] == "REVISION_CONFLICT"
        for value in default_race
    )

    connection = factory.open()
    try:
        selected = PersonalWikiProfileRepository().require_owned(
            connection, owner_user_id="owner", space_id=extra.resource_id
        )
        old = PersonalWikiProfileRepository().require_owned(
            connection, owner_user_id="owner", space_id=default_id
        )
        assert selected.is_default is True and selected.row_version == 2
        assert old.is_default is False and old.row_version == 2
        assert connection.execute(
            "SELECT count(*) FROM audit_event WHERE action='personal_wiki.set_default'"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT count(*) FROM outbox_event WHERE event_type='personal_wiki.default_changed'"
        ).fetchone()[0] == 1
    finally:
        connection.close()

    returned = service.set_default(
        SetDefaultPersonalWikiCommand(
            actor_user_id="owner",
            actor_scope_epoch=1,
            operation_id=_uuid("return-default"),
            request_id=_uuid("return-default-request"),
            space_id=default_id,
            expected_row_version=2,
        )
    )
    assert returned.row_version == 3

    connection = factory.open()
    try:
        target = PersonalWikiProfileRepository().require_owned(
            connection, owner_user_id="owner", space_id=extra.resource_id
        )
        assert target.is_default is False and target.row_version == 3
    finally:
        connection.close()

    archive_race = _race(
        isolated_knowledge_layout.core_db_path,
        "archive",
        extra.resource_id,
        3,
    )
    assert sum(value[0] == "ok" for value in archive_race) == 1, archive_race
    assert all(
        value[0] == "ok" or value[1] == "REVISION_CONFLICT"
        for value in archive_race
    )

    restore_race = _race(
        isolated_knowledge_layout.core_db_path,
        "restore",
        extra.resource_id,
        4,
    )
    assert sum(value[0] == "ok" for value in restore_race) == 1, restore_race
    assert all(
        value[0] == "ok" or value[1] == "REVISION_CONFLICT"
        for value in restore_race
    )

    connection = factory.open()
    try:
        target = PersonalWikiProfileRepository().require_owned(
            connection, owner_user_id="owner", space_id=extra.resource_id
        )
        assert target.lifecycle_status == "active"
        assert target.lifecycle_generation == 3
        assert target.row_version == 5
        assert target.is_default is False
        assert connection.execute(
            "SELECT count(*) FROM audit_event WHERE action='personal_wiki.archive'"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT count(*) FROM audit_event WHERE action='personal_wiki.restore'"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT count(*) FROM outbox_event WHERE event_type='personal_wiki.archived'"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT count(*) FROM outbox_event WHERE event_type='personal_wiki.restored'"
        ).fetchone()[0] == 1
    finally:
        connection.close()

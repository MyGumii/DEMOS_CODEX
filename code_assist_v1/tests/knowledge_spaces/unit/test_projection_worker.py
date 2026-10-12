from __future__ import annotations

import errno
import json
from pathlib import Path
import sqlite3
import time
from typing import Any
from uuid import UUID

import pytest

from code_assist_v1.knowledge_core.db import db_now_ms, transaction
from code_assist_v1.knowledge_core.projection import materializer as materializer_module
from code_assist_v1.knowledge_core.projection.materializer import (
    FullProjectionMaterializer,
    ProjectionMaterializationError,
)
from code_assist_v1.knowledge_core.projection.worker import (
    OutboxProjectionWorker,
    ProjectionWorkerConfig,
)
from code_assist_v1.knowledge_core.repositories.outbox import (
    OutboxClaimFence,
    OutboxClaimLostError,
    OutboxEventDraft,
    OutboxRepository,
)
from code_assist_v1.knowledge_core.repositories.audit import (
    AuditEventDraft,
    AuditRepository,
)
from code_assist_v1.tests.knowledge_spaces.unit.test_legacy_content_importer import (
    _factory,
    _storage_layout,
)
from code_assist_v1.tests.knowledge_spaces.unit.test_legacy_migration_verifier import (
    _publish_first_migrated_item_without_search,
)


pytestmark = pytest.mark.unit


def _uuid(value: int) -> str:
    return str(UUID(int=value))


def _raw_connection(factory: Any) -> sqlite3.Connection:
    connection = sqlite3.connect(factory.database_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA trusted_schema=OFF")
    connection.execute("PRAGMA recursive_triggers=ON")
    return connection


def _seed_space(factory: Any, *, seed: int, user_id: str) -> str:
    space_id = _uuid(seed)
    connection = _raw_connection(factory)
    try:
        connection.execute(
            """INSERT INTO app_user(
                 id,display_name,password_hash,system_role,account_status,
                 current_part_id,scope_epoch,created_at,updated_at,disabled_at,row_version
               ) VALUES (?,?,?,'admin','active',NULL,1,1,1,NULL,1)""",
            (user_id, user_id.title(), "SANITIZED_TEST_HASH"),
        )
        connection.execute(
            """INSERT INTO knowledge_space(
                 id,space_kind,owner_user_id,owner_org_unit_id,status,write_enabled,
                 generation,created_at,updated_at,row_version
               ) VALUES (?,'personal',?,NULL,'active',0,0,1,1,1)""",
            (space_id, user_id),
        )
        connection.execute(
            """INSERT INTO personal_wiki_profile(
                 space_id,owner_user_id,display_name,name_key,lifecycle_status,
                 lifecycle_generation,is_default,archived_at,
                 archived_by_user_id,archive_reason,created_at,updated_at,row_version
               ) VALUES (?,?,?,?,'active',1,1,NULL,NULL,NULL,1,1,1)""",
            (space_id, user_id, f"{user_id} Wiki", f"{user_id} wiki"),
        )
        connection.commit()
        return space_id
    finally:
        connection.close()


def _enqueue_space_generation(
    factory: Any,
    *,
    space_id: str,
    logical_key: str,
    aggregate_id: str,
) -> int:
    repository = OutboxRepository()
    connection = factory.open()
    try:
        with transaction(connection):
            now_ms = db_now_ms(connection)
            row = connection.execute(
                "SELECT generation,row_version FROM knowledge_space WHERE id=?",
                (space_id,),
            ).fetchone()
            assert row is not None
            generation = int(row["generation"]) + 1
            cursor = connection.execute(
                """UPDATE knowledge_space
                      SET generation=?,updated_at=?,row_version=row_version+1
                    WHERE id=? AND row_version=?""",
                (generation, now_ms, space_id, int(row["row_version"])),
            )
            assert cursor.rowcount == 1
            event = repository.append(
                connection,
                OutboxEventDraft(
                    logical_key=logical_key,
                    space_id=space_id,
                    aggregate_type="knowledge_item",
                    aggregate_id=aggregate_id,
                    aggregate_version=generation,
                    event_type="knowledge_item.test_projected",
                    payload={
                        "item_id": aggregate_id,
                        "schema_version": 1,
                        "space_generation": generation,
                    },
                    available_at_ms=now_ms,
                ),
            )
            return event.id
    finally:
        connection.close()


def _enqueue_no_space(factory: Any, logical_key: str) -> int:
    connection = factory.open()
    try:
        with transaction(connection):
            now_ms = db_now_ms(connection)
            return OutboxRepository().append(
                connection,
                OutboxEventDraft(
                    logical_key=logical_key,
                    space_id=None,
                    aggregate_type="account",
                    aggregate_id="projector",
                    aggregate_version=1,
                    event_type="account.test_event",
                    payload={"schema_version": 1},
                    available_at_ms=now_ms,
                ),
            ).id
    finally:
        connection.close()


def _event(factory: Any, event_id: int) -> Any:
    connection = factory.open()
    try:
        with transaction(connection):
            return OutboxRepository().require(connection, event_id)
    finally:
        connection.close()


def _worker(factory: Any, layout: Any, **changes: object) -> OutboxProjectionWorker:
    failpoint = changes.pop("failpoint", None)
    values: dict[str, object] = {
        "claim_owner": "projection-test",
        "lease_ms": 900,
        "heartbeat_interval_ms": 200,
        "idle_poll_ms": 5,
        "maximum_idle_poll_ms": 20,
        "maximum_attempts": 3,
        "retry_delay_ms": 0,
    }
    values.update(changes)
    return OutboxProjectionWorker(
        factory,
        layout,
        ProjectionWorkerConfig(**values),  # type: ignore[arg-type]
        failpoint=failpoint,  # type: ignore[arg-type]
    )


def test_generation_desired_state_and_outbox_commit_or_rollback_together(
    isolated_knowledge_layout: Any,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    space_id = _seed_space(factory, seed=40_900, user_id="projector")
    repository = OutboxRepository()

    connection = factory.open()
    try:
        with pytest.raises(RuntimeError, match="injected rollback"):
            with transaction(connection):
                now_ms = db_now_ms(connection)
                connection.execute(
                    """UPDATE knowledge_space
                          SET generation=1,updated_at=?,row_version=row_version+1
                        WHERE id=? AND generation=0""",
                    (now_ms, space_id),
                )
                repository.append(
                    connection,
                    OutboxEventDraft(
                        logical_key="projection:test:atomic",
                        space_id=space_id,
                        aggregate_type="knowledge_space",
                        aggregate_id=space_id,
                        aggregate_version=1,
                        event_type="knowledge_space.test_changed",
                        payload={"schema_version": 1, "space_generation": 1},
                        available_at_ms=now_ms,
                    ),
                )
                assert connection.execute(
                    """SELECT desired_generation FROM projection_state
                        WHERE projection_name='markdown' AND space_id=?""",
                    (space_id,),
                ).fetchone()[0] == 1
                raise RuntimeError("injected rollback")
    finally:
        connection.close()

    connection = _raw_connection(factory)
    try:
        assert connection.execute(
            "SELECT generation FROM knowledge_space WHERE id=?", (space_id,)
        ).fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM outbox_event").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM projection_state").fetchone()[0] == 0
    finally:
        connection.close()

    event_id = _enqueue_space_generation(
        factory,
        space_id=space_id,
        logical_key="projection:test:atomic",
        aggregate_id=space_id,
    )
    connection = _raw_connection(factory)
    try:
        assert connection.execute(
            "SELECT generation FROM knowledge_space WHERE id=?", (space_id,)
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT state FROM outbox_event WHERE id=?", (event_id,)
        ).fetchone()[0] == "pending"
        assert tuple(
            connection.execute(
                """SELECT desired_generation,built_generation,status
                     FROM projection_state
                    WHERE projection_name='markdown' AND space_id=?""",
                (space_id,),
            ).fetchone()
        ) == (1, 0, "idle")
    finally:
        connection.close()


@pytest.mark.scenario("PROJ-004")
def test_incremental_settlement_matches_full_rebuild_checksum(
    isolated_knowledge_layout: Any,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    layout = _storage_layout(isolated_knowledge_layout)
    space_id = _seed_space(factory, seed=41_000, user_id="projector")
    item_id = _publish_first_migrated_item_without_search(
        factory,
        body="incremental equals full\n",
        id_base=41_010,
        owner_user_id="projector",
        space_id=space_id,
    )
    event_id = _enqueue_space_generation(
        factory,
        space_id=space_id,
        logical_key="projection:test:incremental",
        aggregate_id=item_id,
    )

    incremental = _worker(factory, layout).run_once()
    assert incremental.event is not None
    assert incremental.event.id == event_id
    assert incremental.event.state == "applied"
    assert incremental.projection is not None
    manifest_before = (layout.projection_root / space_id / "manifest.json").read_bytes()

    full = FullProjectionMaterializer(factory, layout).rebuild_all()
    full_space = next(value for value in full.spaces if value.space_id == space_id)
    assert full_space == incremental.projection
    assert (layout.projection_root / space_id / "manifest.json").read_bytes() == (
        manifest_before
    )
    connection = _raw_connection(factory)
    try:
        state = connection.execute(
            """SELECT desired_generation,built_generation,status,checksum_sha256
                 FROM projection_state
                WHERE projection_name='markdown' AND space_id=?""",
            (space_id,),
        ).fetchone()
        assert tuple(state[:3]) == (1, 1, "ready")
        assert state["checksum_sha256"] == incremental.projection.manifest_sha256
    finally:
        connection.close()


@pytest.mark.scenario("TIME-009", "TIME-019")
def test_footer_v2_worker_settles_both_projections_before_ack(
    isolated_knowledge_layout: Any,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    layout = _storage_layout(isolated_knowledge_layout)
    space_id = _seed_space(factory, seed=41_050, user_id="temporal-projector")
    item_id = _publish_first_migrated_item_without_search(
        factory,
        body="canonical only\n",
        id_base=41_060,
        owner_user_id="temporal-projector",
        space_id=space_id,
    )
    event_id = _enqueue_space_generation(
        factory,
        space_id=space_id,
        logical_key="projection:test:footer-v2",
        aggregate_id=item_id,
    )

    result = _worker(factory, layout, footer_v2=True).run_once()
    assert result.event is not None
    assert result.event.id == event_id
    assert result.event.state == "applied"
    generation = layout.projection_root / space_id / "temporal-generations" / "1-0-0"
    source = (generation / "sources" / f"{item_id}.md").read_bytes()
    page = (generation / "pages" / f"{item_id}.md").read_bytes()
    assert source == b"canonical only\n"
    assert page.startswith(source + b"\n\n<!-- knowledge-footer:v2 -->\n")

    connection = _raw_connection(factory)
    try:
        states = connection.execute(
            """SELECT projection_name,desired_generation,built_generation,status
                 FROM projection_state WHERE space_id=? ORDER BY projection_name""",
            (space_id,),
        ).fetchall()
        assert [tuple(row) for row in states] == [
            ("markdown", 1, 1, "ready"),
            ("markdown_v2", 1, 1, "ready"),
        ]
        assert connection.execute(
            "SELECT COUNT(*) FROM projection_manifest_v2_ledger WHERE space_id=?",
            (space_id,),
        ).fetchone()[0] == 1
    finally:
        connection.close()


def test_space_event_rebuild_never_materializes_an_unclaimed_other_space(
    isolated_knowledge_layout: Any,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    layout = _storage_layout(isolated_knowledge_layout)
    first = _seed_space(factory, seed=41_100, user_id="projector-a")
    second = _seed_space(factory, seed=41_101, user_id="projector-b")
    item = _publish_first_migrated_item_without_search(
        factory,
        body="only first",
        id_base=41_110,
        owner_user_id="projector-a",
        space_id=first,
    )
    _publish_first_migrated_item_without_search(
        factory,
        body="must remain canonical only",
        id_base=41_130,
        owner_user_id="projector-b",
        space_id=second,
    )
    _enqueue_space_generation(
        factory,
        space_id=first,
        logical_key="projection:test:isolated",
        aggregate_id=item,
    )

    result = _worker(factory, layout).run_once()
    assert result.event is not None and result.event.state == "applied"
    assert (layout.projection_root / first / "manifest.json").is_file()
    assert not (layout.projection_root / second / "manifest.json").exists()
    connection = _raw_connection(factory)
    try:
        assert connection.execute(
            "SELECT count(*) FROM knowledge_item WHERE space_id=?", (second,)
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT count(*) FROM projection_state WHERE space_id=?", (second,)
        ).fetchone()[0] == 0
    finally:
        connection.close()


def test_every_live_space_generation_writer_has_a_projected_audit_action() -> None:
    repositories = Path(materializer_module.__file__).resolve().parents[1] / "repositories"
    expected = {
        "editing.py": {"knowledge_item.edit"},
        "lifecycle.py": {
            "knowledge_item.deactivate",
            "knowledge_item.restore",
        },
        "merges.py": {"knowledge_item.merge"},
        "proposals.py": {"change_proposal.apply"},
        "publication_commit.py": {"publication.created"},
        "purge.py": {"knowledge_item.purge"},
        "semantic_edges.py": {"semantic_edge.apply"},
    }
    generation_writers = {
        path.name
        for path in repositories.glob("*.py")
        if "SETgeneration=generation+1" in "".join(
            path.read_text(encoding="utf-8").split()
        )
    }
    projected = set(materializer_module._PROJECTED_AUDIT_ACTIONS)

    assert generation_writers == set(expected)
    assert set().union(*expected.values()) <= projected
    assert materializer_module._PROJECTED_AUDIT_ACTIONS == tuple(sorted(projected))


def test_crash_after_manifest_before_ack_reclaims_and_replays_idempotently(
    isolated_knowledge_layout: Any,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    layout = _storage_layout(isolated_knowledge_layout)
    space_id = _seed_space(factory, seed=41_200, user_id="projector")
    item_id = _publish_first_migrated_item_without_search(
        factory,
        body="crash safe",
        id_base=41_210,
        owner_user_id="projector",
        space_id=space_id,
    )
    event_id = _enqueue_space_generation(
        factory,
        space_id=space_id,
        logical_key="projection:test:crash",
        aggregate_id=item_id,
    )

    class CrashAfterMaterialization(OutboxProjectionWorker):
        __slots__ = ()

        def _after_materialized(self, claim: Any, projection: Any) -> None:
            raise SystemExit(97)

    config = ProjectionWorkerConfig(
        claim_owner="crashing-projector",
        lease_ms=300,
        heartbeat_interval_ms=50,
        idle_poll_ms=5,
        maximum_idle_poll_ms=20,
        maximum_attempts=3,
        retry_delay_ms=0,
    )
    with pytest.raises(SystemExit, match="97"):
        CrashAfterMaterialization(factory, layout, config).run_once()
    first_manifest = (layout.projection_root / space_id / "manifest.json").read_bytes()
    assert _event(factory, event_id).state == "processing"

    time.sleep(0.4)
    replay = _worker(
        factory,
        layout,
        claim_owner="replacement-projector",
        lease_ms=900,
        heartbeat_interval_ms=200,
    ).run_once()
    assert replay.recovery.retried_ids == (event_id,)
    assert replay.event is not None and replay.event.state == "applied"
    assert replay.event.attempt_count == 2
    assert (layout.projection_root / space_id / "manifest.json").read_bytes() == (
        first_manifest
    )
    generation = layout.projection_root / space_id / "generations" / "1"
    assert sorted(value.name for value in generation.parent.iterdir()) == ["1"]


@pytest.mark.parametrize(
    "failpoint",
    (
        "projector.after_graph_replace",
        "projector.after_index_replace",
        "projector.after_markdown_replace",
        "projector.after_staging_write",
        "projector.before_outbox_ack",
    ),
)
def test_projection_failpoints_retry_and_converge_without_partial_generation(
    isolated_knowledge_layout: Any,
    failpoint: str,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    layout = _storage_layout(isolated_knowledge_layout)
    space_id = _seed_space(factory, seed=41_220, user_id="projectionfault")
    item_id = _publish_first_migrated_item_without_search(
        factory,
        body="projection fault convergence",
        id_base=41_230,
        owner_user_id="projectionfault",
        space_id=space_id,
    )
    event_id = _enqueue_space_generation(
        factory,
        space_id=space_id,
        logical_key=f"projection:test:fault:{failpoint}",
        aggregate_id=item_id,
    )

    def stop(name: str) -> None:
        if name == failpoint:
            raise RuntimeError(name)

    failed = _worker(
        factory,
        layout,
        claim_owner="faulting-projector",
        failpoint=stop,
    ).run_once()
    assert failed.event is not None
    assert failed.event.id == event_id
    assert failed.event.state == "retry"

    settled = OutboxProjectionWorker(
        factory,
        layout,
        ProjectionWorkerConfig(
            claim_owner="replacement-projector",
            lease_ms=900,
            heartbeat_interval_ms=200,
            idle_poll_ms=5,
            maximum_idle_poll_ms=20,
            maximum_attempts=3,
            retry_delay_ms=0,
        ),
    ).run_once()
    assert settled.event is not None
    assert settled.event.id == event_id
    assert settled.event.state == "applied", settled.event.last_error
    if failpoint == "projector.before_outbox_ack":
        assert settled.projection is None
    else:
        assert settled.projection is not None
    generation = layout.projection_root / space_id / "generations" / "1"
    assert generation.is_dir()
    assert not tuple(
        value
        for value in generation.parent.iterdir()
        if value.name.startswith(".generation-")
    )


def test_projection_kill_after_claim_is_recovered_after_lease_expiry(
    isolated_knowledge_layout: Any,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    layout = _storage_layout(isolated_knowledge_layout)
    event_id = _enqueue_no_space(factory, "projection:test:claim-kill")

    def stop(name: str) -> None:
        if name == "projector.after_claim":
            raise SystemExit(91)

    with pytest.raises(SystemExit, match="91"):
        _worker(
            factory,
            layout,
            claim_owner="claim-killed-projector",
            lease_ms=300,
            heartbeat_interval_ms=50,
            failpoint=stop,
        ).run_once()
    claimed = _event(factory, event_id)
    assert claimed.state == "processing"
    assert claimed.claim_expires_at_ms is not None
    deadline = time.monotonic() + 2
    while _event(factory, event_id).claim_expires_at_ms > int(time.time() * 1000):
        if time.monotonic() >= deadline:
            raise AssertionError("projection claim did not expire")
        time.sleep(0.005)
    recovered = _worker(
        factory,
        layout,
        claim_owner="claim-replacement-projector",
        lease_ms=300,
        heartbeat_interval_ms=50,
    ).run_once()
    assert recovered.recovery.retried_ids == (event_id,)
    assert recovered.event is not None and recovered.event.state == "applied"


@pytest.mark.parametrize(
    "failure_kind",
    ("disk_full", "permission_denied", "atomic_generation_replace"),
)
def test_projection_filesystem_failures_leave_no_false_success_and_converge(
    isolated_knowledge_layout: Any,
    monkeypatch: pytest.MonkeyPatch,
    failure_kind: str,
) -> None:
    from code_assist_v1.tools import legacy_inventory as inventory_module

    factory = _factory(isolated_knowledge_layout)
    layout = _storage_layout(isolated_knowledge_layout)
    space_id = _seed_space(factory, seed=41_240, user_id="projectionfilesystem")
    item_id = _publish_first_migrated_item_without_search(
        factory,
        body="projection filesystem convergence",
        id_base=41_250,
        owner_user_id="projectionfilesystem",
        space_id=space_id,
    )
    event_id = _enqueue_space_generation(
        factory,
        space_id=space_id,
        logical_key=f"projection:test:filesystem:{failure_kind}",
        aggregate_id=item_id,
    )

    original_write = materializer_module._write_relative
    original_rename = inventory_module._nt_rename_relative
    injected = False

    if failure_kind in {"disk_full", "permission_denied"}:
        error_number = errno.ENOSPC if failure_kind == "disk_full" else errno.EACCES

        def fail_write(*args: Any, **kwargs: Any) -> None:
            nonlocal injected
            if not injected:
                injected = True
                raise OSError(error_number, failure_kind)
            original_write(*args, **kwargs)

        monkeypatch.setattr(materializer_module, "_write_relative", fail_write)
    else:

        def fail_generation_rename(
            handle: int,
            root_handle: int,
            name: str,
            *,
            replace: bool,
        ) -> None:
            nonlocal injected
            if name == "1" and not replace and not injected:
                injected = True
                raise OSError(errno.EIO, failure_kind)
            original_rename(
                handle, root_handle, name, replace=replace
            )

        monkeypatch.setattr(
            inventory_module, "_nt_rename_relative", fail_generation_rename
        )

    failed = _worker(
        factory,
        layout,
        claim_owner="filesystem-fault-projector",
    ).run_once()
    assert injected is True
    assert failed.event is not None and failed.event.id == event_id
    assert failed.event.state == "retry"
    assert not (layout.projection_root / space_id / "manifest.json").exists()

    monkeypatch.setattr(materializer_module, "_write_relative", original_write)
    monkeypatch.setattr(
        inventory_module, "_nt_rename_relative", original_rename
    )
    settled = _worker(
        factory,
        layout,
        claim_owner="filesystem-replacement-projector",
    ).run_once()
    assert settled.event is not None and settled.event.id == event_id
    assert settled.event.state == "applied"
    assert (layout.projection_root / space_id / "manifest.json").is_file()


def test_canonical_advance_after_manifest_prevents_stale_ack_and_settles_latest(
    isolated_knowledge_layout: Any,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    layout = _storage_layout(isolated_knowledge_layout)
    space_id = _seed_space(factory, seed=41_250, user_id="projector")
    item_id = _publish_first_migrated_item_without_search(
        factory,
        body="ack fence",
        id_base=41_260,
        owner_user_id="projector",
        space_id=space_id,
    )
    first_event_id = _enqueue_space_generation(
        factory,
        space_id=space_id,
        logical_key="projection:test:ack-fence:first",
        aggregate_id=item_id,
    )

    class AdvanceBeforeAck(OutboxProjectionWorker):
        __slots__ = ("_advanced",)

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            self._advanced = False

        def _after_materialized(self, claim: Any, projection: Any) -> None:
            if self._advanced:
                return
            self._advanced = True
            _enqueue_space_generation(
                factory,
                space_id=space_id,
                logical_key="projection:test:ack-fence:second",
                aggregate_id=item_id,
            )

    config = ProjectionWorkerConfig(
        claim_owner="ack-fence-projector",
        lease_ms=900,
        heartbeat_interval_ms=200,
        idle_poll_ms=5,
        maximum_idle_poll_ms=20,
        maximum_attempts=3,
        retry_delay_ms=0,
    )
    first = AdvanceBeforeAck(factory, layout, config).run_once()
    assert first.event is not None
    assert first.event.id == first_event_id
    assert first.event.state == "retry"
    assert first.event.last_error == {
        "code": "PROJECTION_RETRY",
        "reason": "canonical_drift",
        "schema_version": 1,
    }

    worker = _worker(factory, layout, claim_owner="ack-fence-replacement")
    settlements = (worker.run_once(), worker.run_once())
    assert all(value.event is not None for value in settlements)
    assert {value.event.id for value in settlements if value.event is not None} == {
        first_event_id,
        first_event_id + 1,
    }
    assert all(
        value.event is not None and value.event.state == "applied"
        for value in settlements
    )
    assert sum(
        value.projection is not None and value.projection.generation == 2
        for value in settlements
    ) == 1
    assert sum(value.projection is None for value in settlements) == 1

    manifest = json.loads(
        (layout.projection_root / space_id / "manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["generation"] == 2
    connection = _raw_connection(factory)
    try:
        state = connection.execute(
            """SELECT desired_generation,built_generation,status
                 FROM projection_state
                WHERE projection_name='markdown' AND space_id=?""",
            (space_id,),
        ).fetchone()
        assert tuple(state) == (2, 2, "ready")
        assert connection.execute(
            "SELECT count(*) FROM outbox_event WHERE state='applied'"
        ).fetchone()[0] == 2
    finally:
        connection.close()


def test_stale_event_failure_cannot_poison_a_newer_settled_generation(
    isolated_knowledge_layout: Any,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    layout = _storage_layout(isolated_knowledge_layout)
    space_id = _seed_space(factory, seed=41_275, user_id="projector")
    item_id = _publish_first_migrated_item_without_search(
        factory,
        body="newer settlement wins",
        id_base=41_280,
        owner_user_id="projector",
        space_id=space_id,
    )
    stale_event_id = _enqueue_space_generation(
        factory,
        space_id=space_id,
        logical_key="projection:test:stale-failure:first",
        aggregate_id=item_id,
    )
    repository = OutboxRepository()
    connection = factory.open()
    try:
        with transaction(connection):
            stale_claim = repository.claim_next(
                connection,
                claim_owner="stale-projector",
                lease_ms=30_000,
                now_ms=db_now_ms(connection),
                max_attempts=3,
            )
            assert stale_claim is not None
            assert stale_claim.event.id == stale_event_id
    finally:
        connection.close()


    current_event_id = _enqueue_space_generation(
        factory,
        space_id=space_id,
        logical_key="projection:test:stale-failure:second",
        aggregate_id=item_id,
    )
    current = _worker(
        factory, layout, claim_owner="current-projector"
    ).run_once()
    assert current.event is not None
    assert current.event.id == current_event_id
    assert current.event.state == "applied"
    assert current.projection is not None and current.projection.generation == 2

    connection = _raw_connection(factory)
    try:
        settled_before = tuple(
            connection.execute(
                """SELECT desired_generation,built_generation,status,
                          checksum_sha256,last_error_json,row_version
                     FROM projection_state
                    WHERE projection_name='markdown' AND space_id=?""",
                (space_id,),
            ).fetchone()
        )
    finally:
        connection.close()
    assert settled_before[:3] == (2, 2, "ready")
    assert settled_before[3] is not None and settled_before[4] is None

    terminal = _worker(
        factory,
        layout,
        claim_owner="stale-projector",
        maximum_attempts=1,
    )._record_failure(
        stale_claim,
        ProjectionMaterializationError("PRIVATE-STALE-FAILURE"),
    )
    assert terminal.state == "dead"
    assert "PRIVATE-STALE-FAILURE" not in json.dumps(terminal.last_error)
    connection = _raw_connection(factory)
    try:
        settled_after = tuple(
            connection.execute(
                """SELECT desired_generation,built_generation,status,
                          checksum_sha256,last_error_json,row_version
                     FROM projection_state
                    WHERE projection_name='markdown' AND space_id=?""",
                (space_id,),
            ).fetchone()
        )
    finally:
        connection.close()
    assert settled_after == settled_before


def test_settled_generation_coalesces_redundant_space_events(
    isolated_knowledge_layout: Any,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    layout = _storage_layout(isolated_knowledge_layout)
    space_id = _seed_space(factory, seed=41_276, user_id="projector-batch")
    item_id = _publish_first_migrated_item_without_search(
        factory,
        body="one current generation covers redundant projection triggers",
        id_base=41_286,
        owner_user_id="projector-batch",
        space_id=space_id,
    )
    first_event_id = _enqueue_space_generation(
        factory,
        space_id=space_id,
        logical_key="projection:test:coalesced:first",
        aggregate_id=item_id,
    )
    second_event_id = _enqueue_space_generation(
        factory,
        space_id=space_id,
        logical_key="projection:test:coalesced:second",
        aggregate_id=item_id,
    )

    worker = _worker(
        factory,
        layout,
        claim_owner="coalescing-projector",
        settle_space_batch_size=16,
    )
    settled = worker.run_once()
    assert settled.event is not None
    assert settled.event.id == first_event_id
    assert settled.event.state == "applied"
    assert settled.projection is not None
    assert settled.projection.generation == 2
    assert _event(factory, second_event_id).state == "applied"
    assert worker.run_once().idle


def test_retry_trigger_acknowledges_an_already_settled_generation_without_rebuild(
    isolated_knowledge_layout: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    layout = _storage_layout(isolated_knowledge_layout)
    space_id = _seed_space(factory, seed=41_290, user_id="projector-replay")
    item_id = _publish_first_migrated_item_without_search(
        factory,
        body="the settled immutable generation is reusable",
        id_base=41_300,
        owner_user_id="projector-replay",
        space_id=space_id,
    )
    _enqueue_space_generation(
        factory,
        space_id=space_id,
        logical_key="projection:test:settled-replay:first",
        aggregate_id=item_id,
    )
    assert _worker(factory, layout).run_once().event is not None

    connection = factory.open()
    try:
        with transaction(connection):
            now_ms = db_now_ms(connection)
            generation = int(
                connection.execute(
                    "SELECT generation FROM knowledge_space WHERE id=?",
                    (space_id,),
                ).fetchone()["generation"]
            )
            replay_id = OutboxRepository().append(
                connection,
                OutboxEventDraft(
                    logical_key="projection:test:settled-replay:covered",
                    space_id=space_id,
                    aggregate_type="knowledge_item",
                    aggregate_id=item_id,
                    aggregate_version=generation,
                    event_type="knowledge_item.test_projected",
                    payload={"schema_version": 1, "space_generation": generation},
                    available_at_ms=now_ms,
                ),
            ).id
    finally:
        connection.close()

    def reject_rebuild(*_args: object, **_kwargs: object) -> object:
        pytest.fail("an already-settled generation must not be rebuilt")

    monkeypatch.setattr(FullProjectionMaterializer, "rebuild_space", reject_rebuild)
    replay = _worker(factory, layout).run_once()
    assert replay.event is not None
    assert replay.event.id == replay_id
    assert replay.event.state == "applied"
    assert replay.projection is None


def test_invalid_projection_retries_then_dies_without_secret_error_text(
    isolated_knowledge_layout: Any,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    layout = _storage_layout(isolated_knowledge_layout)
    space_id = _seed_space(factory, seed=41_300, user_id="projector")
    item_id = _publish_first_migrated_item_without_search(
        factory,
        body="PRIVATE-SENTINEL\r\n",
        id_base=41_310,
        owner_user_id="projector",
        space_id=space_id,
    )
    event_id = _enqueue_space_generation(
        factory,
        space_id=space_id,
        logical_key="projection:test:invalid",
        aggregate_id=item_id,
    )
    worker = _worker(factory, layout, maximum_attempts=2)

    first = worker.run_once()
    assert first.event is not None and first.event.state == "retry"
    second = worker.run_once()
    assert second.event is not None and second.event.state == "dead"
    assert second.event.id == event_id
    encoded = json.dumps(second.event.last_error, sort_keys=True)
    assert "PRIVATE-SENTINEL" not in encoded
    assert second.event.last_error == {
        "code": "PROJECTION_RETRY",
        "reason": "materialization_failed",
        "schema_version": 1,
    }
    assert not (layout.projection_root / space_id / "manifest.json").exists()
    connection = _raw_connection(factory)
    try:
        state = connection.execute(
            """SELECT status,last_error_json FROM projection_state
                WHERE projection_name='markdown' AND space_id=?""",
            (space_id,),
        ).fetchone()
        assert state["status"] == "error"
        assert "PRIVATE-SENTINEL" not in state["last_error_json"]
    finally:
        connection.close()


def test_no_space_event_is_an_explicit_projection_no_op(
    isolated_knowledge_layout: Any,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    layout = _storage_layout(isolated_knowledge_layout)
    event_id = _enqueue_no_space(factory, "projection:test:no-space")

    result = _worker(factory, layout).run_once()
    assert result.projection is None
    assert result.event is not None
    assert result.event.id == event_id
    assert result.event.state == "applied"
    assert list(layout.projection_root.iterdir()) == []


@pytest.mark.parametrize(
    ("audit_action", "audit_outcome", "event_type"),
    (
        ("knowledge_lease.acquired", "acquired", "knowledge_lease.acquired"),
        ("curation_finding.review", "approved", "curation.finding.approved"),
    ),
)
def test_non_file_audit_event_does_not_rewrite_an_immutable_generation(
    isolated_knowledge_layout: Any,
    audit_action: str,
    audit_outcome: str,
    event_type: str,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    layout = _storage_layout(isolated_knowledge_layout)
    space_id = _seed_space(factory, seed=41_350, user_id="projector")
    item_id = _publish_first_migrated_item_without_search(
        factory,
        body="stable generation",
        id_base=41_360,
        owner_user_id="projector",
        space_id=space_id,
    )
    _enqueue_space_generation(
        factory,
        space_id=space_id,
        logical_key="projection:test:file-visible",
        aggregate_id=item_id,
    )
    first = _worker(factory, layout).run_once()
    assert first.event is not None and first.event.state == "applied"
    manifest_path = layout.projection_root / space_id / "manifest.json"
    manifest_before = manifest_path.read_bytes()
    log_before = (
        layout.projection_root / space_id / "generations" / "1" / "log.md"
    ).read_bytes()

    connection = factory.open()
    try:
        with transaction(connection):
            now_ms = db_now_ms(connection)
            AuditRepository().append(
                connection,
                AuditEventDraft(
                    event_uuid=_uuid(41_380),
                    occurred_at_ms=now_ms,
                    request_id=_uuid(41_381),
                    operation_id=_uuid(41_382),
                    action=audit_action,
                    outcome=audit_outcome,
                    actor_kind="user",
                    actor_user_id="projector",
                    actor_display_name="Projector",
                    actor_role_snapshot="admin",
                    space_id=space_id,
                    item_id=item_id,
                    details={"schema_version": 1},
                    request_meta={},
                ),
            )
            OutboxRepository().append(
                connection,
                OutboxEventDraft(
                    logical_key=f"projection:test:audit-only:{audit_action}",
                    space_id=space_id,
                    aggregate_type="knowledge_item",
                    aggregate_id=item_id,
                    aggregate_version=1,
                    event_type=event_type,
                    payload={"schema_version": 1},
                    available_at_ms=now_ms,
                ),
            )
    finally:
        connection.close()

    replay = _worker(factory, layout).run_once()
    assert replay.event is not None and replay.event.state == "applied"
    assert manifest_path.read_bytes() == manifest_before
    assert (
        layout.projection_root / space_id / "generations" / "1" / "log.md"
    ).read_bytes() == log_before


def test_long_projection_renews_the_durable_claim(
    isolated_knowledge_layout: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    layout = _storage_layout(isolated_knowledge_layout)
    space_id = _seed_space(factory, seed=41_400, user_id="projector")
    item_id = _publish_first_migrated_item_without_search(
        factory,
        body="heartbeat",
        id_base=41_410,
        owner_user_id="projector",
        space_id=space_id,
    )
    _enqueue_space_generation(
        factory,
        space_id=space_id,
        logical_key="projection:test:heartbeat",
        aggregate_id=item_id,
    )
    original = materializer_module.FullProjectionMaterializer.rebuild_space

    def slow_rebuild(self: Any, selected_space_id: str) -> Any:
        time.sleep(0.45)
        return original(self, selected_space_id)

    monkeypatch.setattr(
        materializer_module.FullProjectionMaterializer,
        "rebuild_space",
        slow_rebuild,
    )
    result = _worker(
        factory,
        layout,
        lease_ms=600,
        heartbeat_interval_ms=100,
    ).run_once()
    assert result.event is not None and result.event.state == "applied"
    assert result.heartbeat_count >= 3


def test_repository_claim_fence_recovery_and_exact_terminal_transition(
    isolated_knowledge_layout: Any,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    event_id = _enqueue_no_space(factory, "projection:test:repository")
    repository = OutboxRepository()

    connection = factory.open()
    try:
        with transaction(connection):
            now_ms = db_now_ms(connection)
            claim = repository.claim_next(
                connection,
                claim_owner="repository-test",
                lease_ms=300,
                now_ms=now_ms,
                max_attempts=3,
            )
            assert claim is not None and claim.event.id == event_id
            with pytest.raises(OutboxClaimLostError):
                repository.mark_applied(
                    connection,
                    OutboxClaimFence(event_id, "wrong-owner", 1),
                    now_ms=now_ms,
                )
    finally:
        connection.close()

    time.sleep(0.35)
    connection = factory.open()
    try:
        with transaction(connection):
            now_ms = db_now_ms(connection)
            recovery = repository.recover_expired(
                connection,
                now_ms=now_ms,
                max_attempts=3,
                error={"code": "expired", "schema_version": 1},
            )
            assert recovery.retried_ids == (event_id,)
            claim = repository.claim_next(
                connection,
                claim_owner="repository-test-2",
                lease_ms=300,
                now_ms=now_ms,
                max_attempts=3,
            )
            assert claim is not None and claim.event.attempt_count == 2
            applied = repository.mark_applied(
                connection, claim.fence, now_ms=now_ms
            )
            assert applied.state == "applied"
            assert applied.completed_at_ms == now_ms
    finally:
        connection.close()


def test_repository_parallel_claims_serialize_each_space_lane(
    isolated_knowledge_layout: Any,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    first_space = _seed_space(factory, seed=51_001, user_id="projector-a")
    second_space = _seed_space(factory, seed=51_002, user_id="projector-b")
    first_id = _enqueue_space_generation(
        factory,
        space_id=first_space,
        logical_key="projection:test:parallel:first",
        aggregate_id=_uuid(51_011),
    )
    blocked_id = _enqueue_space_generation(
        factory,
        space_id=first_space,
        logical_key="projection:test:parallel:blocked",
        aggregate_id=_uuid(51_012),
    )
    independent_id = _enqueue_space_generation(
        factory,
        space_id=second_space,
        logical_key="projection:test:parallel:independent",
        aggregate_id=_uuid(51_013),
    )
    repository = OutboxRepository()

    connection = factory.open()
    try:
        with transaction(connection):
            now_ms = db_now_ms(connection)
            first = repository.claim_next(
                connection,
                claim_owner="parallel-projector-a",
                lease_ms=30_000,
                now_ms=now_ms,
                max_attempts=3,
            )
            assert first is not None and first.event.id == first_id
    finally:
        connection.close()

    connection = factory.open()
    try:
        with transaction(connection):
            now_ms = db_now_ms(connection)
            independent = repository.claim_next(
                connection,
                claim_owner="parallel-projector-b",
                lease_ms=30_000,
                now_ms=now_ms,
                max_attempts=3,
            )
            assert independent is not None
            assert independent.event.id == independent_id
    finally:
        connection.close()
    assert _event(factory, blocked_id).state == "pending"


def test_projection_worker_config_rejects_unsafe_lease_shapes() -> None:
    with pytest.raises(ValueError, match="at least 300"):
        ProjectionWorkerConfig(claim_owner="worker", lease_ms=299)
    with pytest.raises(ValueError, match="one third"):
        ProjectionWorkerConfig(
            claim_owner="worker", lease_ms=300, heartbeat_interval_ms=100
        )
    with pytest.raises(ValueError, match="claim_owner"):
        ProjectionWorkerConfig(claim_owner=" worker ")

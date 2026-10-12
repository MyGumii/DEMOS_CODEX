from __future__ import annotations

import multiprocessing
import os
from pathlib import Path
import time
from typing import Any

import pytest

from code_assist_v1.knowledge_core.db import ConnectionFactory
from code_assist_v1.knowledge_core.migrations import CANONICAL_MIGRATION_CATALOG
from code_assist_v1.knowledge_core.migrations.storage_preflight import (
    MigrationStorageLayout,
)
from code_assist_v1.knowledge_core.projection.worker import (
    OutboxProjectionWorker,
    ProjectionWorkerConfig,
)
from code_assist_v1.tests.knowledge_spaces.unit.test_legacy_content_importer import (
    _factory,
    _storage_layout,
)
from code_assist_v1.tests.knowledge_spaces.unit.test_legacy_migration_verifier import (
    _publish_first_migrated_item_without_search,
)
from code_assist_v1.tests.knowledge_spaces.unit.test_projection_worker import (
    _enqueue_space_generation,
    _event,
    _seed_space,
)


pytestmark = pytest.mark.integration


def _crash_after_projection_publish(
    database: str,
    data_root: str,
    blob_root: str,
    projection_root: str,
    published: Any,
) -> None:
    factory = ConnectionFactory._for_isolated_tests(
        Path(database), CANONICAL_MIGRATION_CATALOG
    )
    layout = MigrationStorageLayout(
        data_root=Path(data_root),
        database=Path(database),
        blob_root=Path(blob_root),
        projection_root=Path(projection_root),
    )

    class CrashingProjectionWorker(OutboxProjectionWorker):
        __slots__ = ("_published",)

        def __init__(self) -> None:
            super().__init__(
                factory,
                layout,
                ProjectionWorkerConfig(
                    claim_owner="projection-crash-child",
                    lease_ms=600,
                    heartbeat_interval_ms=100,
                    idle_poll_ms=5,
                    maximum_idle_poll_ms=20,
                    maximum_attempts=3,
                    retry_delay_ms=0,
                ),
            )
            self._published = published

        def _after_materialized(self, claim: Any, projection: Any) -> None:
            self._published.set()
            os._exit(73)

    CrashingProjectionWorker().run_once()
    os._exit(74)


def _run_projector_once(
    database: str,
    data_root: str,
    blob_root: str,
    projection_root: str,
    owner: str,
    result_queue: Any,
) -> None:
    factory = ConnectionFactory._for_isolated_tests(
        Path(database), CANONICAL_MIGRATION_CATALOG
    )
    layout = MigrationStorageLayout(
        data_root=Path(data_root),
        database=Path(database),
        blob_root=Path(blob_root),
        projection_root=Path(projection_root),
    )
    try:
        result = OutboxProjectionWorker(
            factory,
            layout,
            ProjectionWorkerConfig(
                claim_owner=owner,
                lease_ms=900,
                heartbeat_interval_ms=200,
                idle_poll_ms=5,
                maximum_idle_poll_ms=20,
                maximum_attempts=3,
                retry_delay_ms=0,
            ),
        ).run_once()
        result_queue.put(
            (
                "ok",
                result.idle,
                None if result.event is None else result.event.id,
                None if result.event is None else result.event.state,
            )
        )
    except BaseException as exc:
        result_queue.put(("error", type(exc).__name__))


@pytest.mark.scenario("PROJ-002")
def test_real_process_kill_after_manifest_before_ack_replays_without_duplicates(
    isolated_knowledge_layout: Any,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    layout = _storage_layout(isolated_knowledge_layout)
    space_id = _seed_space(factory, seed=42_000, user_id="projector")
    item_id = _publish_first_migrated_item_without_search(
        factory,
        body="real process crash\n",
        id_base=42_010,
        owner_user_id="projector",
        space_id=space_id,
    )
    event_id = _enqueue_space_generation(
        factory,
        space_id=space_id,
        logical_key="projection:process-crash",
        aggregate_id=item_id,
    )

    context = multiprocessing.get_context("spawn")
    published = context.Event()
    process = context.Process(
        target=_crash_after_projection_publish,
        args=(
            str(layout.database),
            str(layout.data_root),
            str(layout.blob_root),
            str(layout.projection_root),
            published,
        ),
    )
    process.start()
    assert published.wait(timeout=30)
    process.join(timeout=10)
    assert not process.is_alive()
    assert process.exitcode == 73

    manifest_path = layout.projection_root / space_id / "manifest.json"
    published_manifest = manifest_path.read_bytes()
    crashed = _event(factory, event_id)
    assert crashed.state == "processing"
    assert crashed.claim_expires_at_ms is not None
    wait_seconds = max(
        0.0,
        (crashed.claim_expires_at_ms - int(time.time() * 1000)) / 1000 + 0.2,
    )
    time.sleep(wait_seconds)

    replacement = OutboxProjectionWorker(
        factory,
        layout,
        ProjectionWorkerConfig(
            claim_owner="projection-replacement",
            lease_ms=600,
            heartbeat_interval_ms=100,
            idle_poll_ms=5,
            maximum_idle_poll_ms=20,
            maximum_attempts=3,
            retry_delay_ms=0,
        ),
    ).run_once()
    assert replacement.recovery.retried_ids == (event_id,)
    assert replacement.event is not None
    assert replacement.event.state == "applied"
    assert replacement.event.attempt_count == 2
    assert manifest_path.read_bytes() == published_manifest
    generations = layout.projection_root / space_id / "generations"
    assert [value.name for value in generations.iterdir()] == ["1"]


def test_two_real_projector_processes_claim_one_event_exactly_once(
    isolated_knowledge_layout: Any,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    layout = _storage_layout(isolated_knowledge_layout)
    space_id = _seed_space(factory, seed=42_100, user_id="projector")
    item_id = _publish_first_migrated_item_without_search(
        factory,
        body="two process claim\n",
        id_base=42_110,
        owner_user_id="projector",
        space_id=space_id,
    )
    event_id = _enqueue_space_generation(
        factory,
        space_id=space_id,
        logical_key="projection:two-process",
        aggregate_id=item_id,
    )

    context = multiprocessing.get_context("spawn")
    result_queue = context.Queue()
    args = (
        str(layout.database),
        str(layout.data_root),
        str(layout.blob_root),
        str(layout.projection_root),
    )
    processes = [
        context.Process(
            target=_run_projector_once,
            args=(*args, f"projector-{index}", result_queue),
        )
        for index in range(2)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=30)
        assert not process.is_alive()
        assert process.exitcode == 0
    results = [result_queue.get(timeout=5) for _ in processes]
    assert all(value[0] == "ok" for value in results)
    claimed = [value for value in results if value[1] is False]
    idle = [value for value in results if value[1] is True]
    assert claimed == [("ok", False, event_id, "applied")]
    assert len(idle) == 1
    final = _event(factory, event_id)
    assert final.state == "applied"
    assert final.attempt_count == 1
    assert (layout.projection_root / space_id / "manifest.json").is_file()

from __future__ import annotations

from contextlib import contextmanager
import threading

import pytest

from code_assist_v1.knowledge_core.checkpoint import (
    CheckpointCoordinatorError,
    PassiveCheckpointCoordinator,
    PassiveCheckpointResult,
)
from code_assist_v1.knowledge_core.db import (
    ConnectionFactory,
    passive_wal_checkpoint,
)
from code_assist_v1.knowledge_core.migrations import (
    CANONICAL_MIGRATION_CATALOG,
    MigrationStorageLayout,
    SingleMigrator,
)


pytestmark = pytest.mark.unit


def _factory(layout) -> ConnectionFactory:
    factory = ConnectionFactory._for_isolated_tests(
        layout.core_db_path, CANONICAL_MIGRATION_CATALOG
    )
    SingleMigrator(
        factory,
        app_version="p7.5-checkpoint-test",
        storage_layout=MigrationStorageLayout(
            data_root=layout.root,
            database=layout.core_db_path,
            blob_root=layout.blobs_dir,
            projection_root=layout.projections_dir,
        ),
    ).run()
    return factory


def test_passive_checkpoint_uses_the_narrow_managed_database_capability(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    busy, log_frames, checkpointed_frames = passive_wal_checkpoint(factory)
    assert busy >= 0
    assert log_frames >= 0
    assert checkpointed_frames >= 0


def test_checkpoint_coordinator_runs_under_one_lifetime_ownership_context(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    calls: list[str] = []

    @contextmanager
    def ownership(path):
        assert path == factory.database_path
        calls.append("enter")
        try:
            yield "owned"
        finally:
            calls.append("exit")

    observations = iter(((0, 7, 5), (1, 11, 3)))

    def checkpoint(received: ConnectionFactory) -> tuple[int, int, int]:
        assert received is factory
        calls.append("checkpoint")
        return next(observations)

    class StopAfterTwoWaits:
        def __init__(self) -> None:
            self.waits = 0

        def is_set(self) -> bool:
            return False

        def wait(self, timeout: float | None = None) -> bool:
            assert timeout == 9
            self.waits += 1
            return self.waits == 2

    result = PassiveCheckpointCoordinator(
        factory,
        interval_seconds=9,
        checkpoint=checkpoint,
        ownership=ownership,
    ).serve(StopAfterTwoWaits())
    assert result.attempts == 2
    assert result.busy_count == 1
    assert result.log_frames == 18
    assert result.checkpointed_frames == 8
    assert calls == ["enter", "checkpoint", "checkpoint", "exit"]


def test_checkpoint_result_rejects_invalid_sqlite_counters() -> None:
    with pytest.raises(ValueError):
        PassiveCheckpointResult(True, 0, 0)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        PassiveCheckpointResult(0, -1, 0)


def test_checkpoint_ownership_provider_must_return_a_context(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    stop = threading.Event()
    coordinator = PassiveCheckpointCoordinator(
        factory,
        checkpoint=lambda _factory: (0, 0, 0),
        ownership=lambda _path: object(),
    )
    with pytest.raises(CheckpointCoordinatorError, match="invalid context"):
        coordinator.serve(stop)

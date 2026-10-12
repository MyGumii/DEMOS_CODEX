from __future__ import annotations

import json
import os
from pathlib import Path
import random
import subprocess
import sys
import time
from uuid import UUID, uuid5

import pytest

from code_assist_v1.knowledge_core.db import ConnectionFactory, db_now_ms, transaction
from code_assist_v1.knowledge_core.domain import FeatureFlags
from code_assist_v1.knowledge_core.migrations import (
    CANONICAL_MIGRATION_CATALOG,
    MigrationStorageLayout,
    SingleMigrator,
)
from code_assist_v1.knowledge_core.repositories.jobs import (
    ClaimFence,
    HandlerKey,
    JobClaimLostError,
    JobEnqueueDraft,
    JobRepository,
)
from code_assist_v1.knowledge_core.repositories.users import (
    LegacyUserRecord,
    UserRepository,
)
from code_assist_v1.knowledge_core.scope import ScopeResolver
from code_assist_v1.knowledge_core.worker import (
    DurableWorkKernel,
    HandlerRegistry,
    PreparedJobEffect,
    RegisteredJobHandler,
)


pytestmark = [pytest.mark.integration, pytest.mark.concurrency]
_NAMESPACE = UUID("08807941-92d8-4858-960d-9aa8aa8b0ba9")
_PACKAGE_ROOT = Path(__file__).resolve().parents[3]
_HANDLER = HandlerKey("fixture.effect", "v1")


def _uuid(label: str) -> str:
    return str(uuid5(_NAMESPACE, label))


def _factory(layout) -> ConnectionFactory:
    factory = ConnectionFactory._for_isolated_tests(
        layout.core_db_path, CANONICAL_MIGRATION_CATALOG
    )
    SingleMigrator(
        factory,
        app_version="p1.9-durable-job-multiprocess-test",
        storage_layout=MigrationStorageLayout(
            data_root=layout.root,
            database=layout.core_db_path,
            blob_root=layout.blobs_dir,
            projection_root=layout.projections_dir,
        ),
    ).run()
    return factory


def _handler(*, actor_kind: str = "system") -> RegisteredJobHandler:
    def prepare(job):
        return PreparedJobEffect(
            result={"effect_key": job.effect_key, "schema_version": 1}
        )

    def commit_effect(connection, job, prepared):
        del prepared
        now_ms = db_now_ms(connection)
        connection.execute(
            """INSERT INTO outbox_event(
                 logical_key,space_id,aggregate_type,aggregate_id,
                 aggregate_version,event_type,payload_json,
                 payload_schema_version,state,available_at,claim_owner,
                 claim_expires_at,attempt_count,last_error_json,created_at,
                 completed_at
               ) VALUES (?,NULL,'durable_job',?,1,'fixture.effect','{}',1,
                 'pending',?,NULL,NULL,0,NULL,?,NULL)""",
            (f"job-effect:{job.effect_key}", job.id, now_ms, now_ms),
        )

    return RegisteredJobHandler.build(
        job_type=_HANDLER.job_type,
        handler_version=_HANDLER.handler_version,
        actor_kind=actor_kind,  # type: ignore[arg-type]
        required_capability="edit" if actor_kind == "user" else None,
        prepare=prepare,
        commit_effect=commit_effect,
    )


def _kernel(
    factory: ConnectionFactory,
    *,
    actor_kind: str = "system",
) -> DurableWorkKernel:
    return DurableWorkKernel(
        factory,
        HandlerRegistry((_handler(actor_kind=actor_kind),)),
        scope_resolver=(
            ScopeResolver(
                FeatureFlags(
                    core_enabled=True,
                    personal_reads=True,
                    personal_writes=True,
                )
            )
            if actor_kind == "user"
            else None
        ),
    )


def _draft(
    connection,
    label: str,
    *,
    actor_kind: str = "system",
    actor_user_id: str | None = None,
    space_id: str | None = None,
    scope_epoch: int | None = None,
    cancellable: bool = True,
    max_attempts: int = 3,
) -> JobEnqueueDraft:
    now_ms = db_now_ms(connection)
    return JobEnqueueDraft(
        job_id=_uuid(f"job:{label}"),
        dedup_id=None,
        operation_id=None,
        actor_kind=actor_kind,  # type: ignore[arg-type]
        actor_user_id=actor_user_id,
        space_id=space_id,
        job_type=_HANDLER.job_type,
        handler_version=_HANDLER.handler_version,
        effect_key=f"fixture-effect:{label}",
        input_ref={"label": label},
        expected_state={"schema_version": 1},
        authorization_snapshot=(
            {"actor_kind": "system", "schema_version": 1}
            if actor_kind == "system"
            else {}
        ),
        authorization_scope_epoch=scope_epoch,
        authorization_part_id=None,
        authorization_capabilities=(),
        max_attempts=max_attempts,
        cancellable=cancellable,
        available_at_ms=now_ms,
        created_at_ms=None,
    )


def _enqueue(
    factory: ConnectionFactory,
    label: str,
    *,
    actor_kind: str = "system",
    actor_user_id: str | None = None,
    space_id: str | None = None,
    scope_epoch: int | None = None,
    cancellable: bool = True,
    max_attempts: int = 3,
):
    connection = factory.open()
    try:
        with transaction(connection):
            return _kernel(factory, actor_kind=actor_kind).enqueue_in_transaction(
                connection,
                _draft(
                    connection,
                    label,
                    actor_kind=actor_kind,
                    actor_user_id=actor_user_id,
                    space_id=space_id,
                    scope_epoch=scope_epoch,
                    cancellable=cancellable,
                    max_attempts=max_attempts,
                ),
            ).job
    finally:
        connection.close()


_WORKER = r"""
import json
import os
from pathlib import Path
import sys
import time

from code_assist_v1.knowledge_core.db import ConnectionFactory, db_now_ms, transaction
from code_assist_v1.knowledge_core.domain import FeatureFlags
from code_assist_v1.knowledge_core.migrations import CANONICAL_MIGRATION_CATALOG
from code_assist_v1.knowledge_core.repositories.jobs import ClaimFence, JobEnqueueDraft
from code_assist_v1.knowledge_core.scope import ScopeResolver
from code_assist_v1.knowledge_core.worker import (
    DurableWorkKernel,
    HandlerRegistry,
    PreparedJobEffect,
    RegisteredJobHandler,
)

mode, database_raw, payload_raw = sys.argv[1:]
database = Path(database_raw)
payload = json.loads(payload_raw)
factory = ConnectionFactory._for_isolated_tests(database, CANONICAL_MIGRATION_CATALOG)

def prepare(job):
    return PreparedJobEffect(result={"effect_key": job.effect_key, "schema_version": 1})

def commit_effect(connection, job, prepared):
    del prepared
    now_ms = db_now_ms(connection)
    connection.execute(
        '''INSERT INTO outbox_event(
             logical_key,space_id,aggregate_type,aggregate_id,aggregate_version,
             event_type,payload_json,payload_schema_version,state,available_at,
             claim_owner,claim_expires_at,attempt_count,last_error_json,created_at,
             completed_at
           ) VALUES (?,NULL,'durable_job',?,1,'fixture.effect','{}',1,'pending',?,
             NULL,NULL,0,NULL,?,NULL)''',
        ("job-effect:" + job.effect_key, job.id, now_ms, now_ms),
    )

actor_kind = payload.get("actor_kind", "system")
handler = RegisteredJobHandler.build(
    job_type="fixture.effect",
    handler_version="v1",
    actor_kind=actor_kind,
    required_capability="edit" if actor_kind == "user" else None,
    prepare=prepare,
    commit_effect=commit_effect,
)
kernel = DurableWorkKernel(
    factory,
    HandlerRegistry((handler,)),
    scope_resolver=(
        ScopeResolver(FeatureFlags(
            core_enabled=True,
            personal_reads=True,
            personal_writes=True,
        ))
        if actor_kind == "user" else None
    ),
)

def wait_for(path_raw):
    path = Path(path_raw)
    deadline = time.monotonic() + 20.0
    while not path.exists():
        if time.monotonic() >= deadline:
            raise SystemExit("barrier timed out")
        time.sleep(0.005)

if mode == "enqueue_kill":
    connection = factory.open()
    try:
        with transaction(connection):
            now_ms = db_now_ms(connection)
            draft = JobEnqueueDraft(
                job_id=payload["job_id"], dedup_id=None, operation_id=None,
                actor_kind="system", actor_user_id=None, space_id=None,
                job_type="fixture.effect", handler_version="v1",
                effect_key=payload["effect_key"],
                input_ref={"label": payload["label"]},
                expected_state={"schema_version": 1},
                authorization_snapshot={"actor_kind": "system", "schema_version": 1},
                authorization_scope_epoch=None, authorization_part_id=None,
                authorization_capabilities=(), max_attempts=3, cancellable=True,
                available_at_ms=now_ms, created_at_ms=None,
            )
            kernel.enqueue_in_transaction(connection, draft)
    finally:
        connection.close()
    os._exit(79)

if mode in ("claim_exit", "run_crash", "race_claim", "user_commit_wait"):
    if payload.get("start"):
        Path(payload["ready"]).touch()
        wait_for(payload["start"])
    recovery, claim = kernel.recover_and_claim(
        claim_owner=payload["owner"], lease_ms=payload["lease_ms"]
    )
    response = {
        "claim": None if claim is None else {
            "attempt_count": claim.fence.attempt_count,
            "claim_owner": claim.fence.claim_owner,
            "job_id": claim.fence.job_id,
            "state": claim.job.state,
            "expires_at": claim.job.claim_expires_at_ms,
        },
        "dead_ids": list(recovery.dead_ids),
        "retried_ids": list(recovery.retried_ids),
    }
    if hasattr(recovery, "cancelled_ids"):
        response["cancelled_ids"] = list(recovery.cancelled_ids)
    if payload.get("result_file"):
        Path(payload["result_file"]).write_text(json.dumps(response, sort_keys=True), encoding="utf-8")
    if claim is None:
        print(json.dumps(response, sort_keys=True), flush=True)
        raise SystemExit(0)
    if mode == "claim_exit":
        os._exit(73)
    if mode == "run_crash":
        kernel.run_claim(claim)
        os._exit(74)
    if mode == "race_claim":
        print(json.dumps(response, sort_keys=True), flush=True)
        raise SystemExit(0)
    connection = factory.open()
    try:
        with transaction(connection):
            started = kernel._jobs.start(connection, claim.fence, now_ms=db_now_ms(connection))
    finally:
        connection.close()
    prepared = prepare(started)
    Path(payload["prepared"]).touch()
    wait_for(payload["commit"])
    result = kernel.commit_prepared(claim.fence, prepared)
    print(json.dumps({
        "job_state": result.job.state,
        "error": result.job.error,
        "replayed": result.replayed,
    }, sort_keys=True), flush=True)
    raise SystemExit(0)

if mode == "replay":
    fence = ClaimFence(
        payload["job_id"], payload["claim_owner"], payload["attempt_count"]
    )
    prepared = PreparedJobEffect(result=payload["result"])
    result = kernel.commit_prepared(fence, prepared)
    print(json.dumps({
        "job_state": result.job.state,
        "result": result.job.result,
        "replayed": result.replayed,
    }, sort_keys=True), flush=True)
    raise SystemExit(0)

raise SystemExit("unknown worker mode")
"""


def _spawn(layout, mode: str, payload: dict[str, object]):
    return subprocess.Popen(
        [
            sys.executable,
            "-c",
            _WORKER,
            mode,
            str(layout.core_db_path),
            json.dumps(payload, sort_keys=True),
        ],
        cwd=_PACKAGE_ROOT.parent,
        env=os.environ.copy(),
        text=True,
        encoding="utf-8",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _communicate_json(process, *, expected_codes: tuple[int, ...] = (0,)):
    stdout, stderr = process.communicate(timeout=40)
    assert process.returncode in expected_codes, (process.returncode, stdout, stderr)
    assert stderr == ""
    lines = [line for line in stdout.splitlines() if line.strip()]
    return None if not lines else json.loads(lines[-1])


def _wait_paths(paths: tuple[Path, ...], processes: tuple[subprocess.Popen, ...]) -> None:
    deadline = time.monotonic() + 20.0
    while not all(path.exists() for path in paths):
        for process in processes:
            if process.poll() is not None:
                stdout, stderr = process.communicate()
                raise AssertionError(
                    f"worker exited before barrier: {process.returncode}; "
                    f"stdout={stdout!r}; stderr={stderr!r}"
                )
        if time.monotonic() >= deadline:
            raise AssertionError("worker barrier timed out")
        time.sleep(0.005)


def _wait_until_db_time(factory: ConnectionFactory, boundary_ms: int) -> None:
    deadline = time.monotonic() + 10.0
    while True:
        connection = factory.open(read_only=True)
        try:
            observed = db_now_ms(connection)
        finally:
            connection.close()
        if observed >= boundary_ms:
            return
        if time.monotonic() >= deadline:
            raise AssertionError("SQLite database clock did not reach claim expiry")
        time.sleep(0.002)


@pytest.mark.scenario("JOB-001")
def test_JOB_001_process_kill_after_enqueue_loses_zero_jobs(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    payload = {
        "job_id": _uuid("job:enqueue-kill"),
        "effect_key": "fixture-effect:enqueue-kill",
        "label": "enqueue-kill",
    }
    process = _spawn(isolated_knowledge_layout, "enqueue_kill", payload)
    _communicate_json(process, expected_codes=(79,))

    connection = factory.open(read_only=True)
    try:
        row = connection.execute(
            "SELECT state,attempt_count,effect_key FROM durable_job WHERE id=?",
            (payload["job_id"],),
        ).fetchone()
        assert tuple(row) == ("queued", 0, payload["effect_key"])
    finally:
        connection.close()


@pytest.mark.scenario("JOB-002")
def test_JOB_002_expired_running_claim_is_reclaimed_with_attempt_ABA_fence(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    job = _enqueue(factory, "reclaim", max_attempts=3)
    result_files = (
        isolated_knowledge_layout.root / "claim-first.json",
        isolated_knowledge_layout.root / "claim-second.json",
    )
    first = _spawn(
        isolated_knowledge_layout,
        "claim_exit",
        {"owner": "worker-first", "lease_ms": 5, "result_file": str(result_files[0])},
    )
    _communicate_json(first, expected_codes=(73,))
    first_result = json.loads(result_files[0].read_text(encoding="utf-8"))
    first_claim = first_result["claim"]
    assert first_claim["job_id"] == job.id
    assert first_claim["attempt_count"] == 1
    _wait_until_db_time(factory, first_claim["expires_at"])

    second = _spawn(
        isolated_knowledge_layout,
        "claim_exit",
        {"owner": "worker-second", "lease_ms": 10_000, "result_file": str(result_files[1])},
    )
    _communicate_json(second, expected_codes=(73,))
    second_result = json.loads(result_files[1].read_text(encoding="utf-8"))
    second_claim = second_result["claim"]
    assert second_result["retried_ids"] == [job.id]
    assert second_claim["attempt_count"] == 2
    assert second_claim["claim_owner"] == "worker-second"

    connection = factory.open()
    try:
        with transaction(connection):
            with pytest.raises(JobClaimLostError):
                JobRepository().start(
                    connection,
                    ClaimFence(job.id, "worker-first", 1),
                    now_ms=db_now_ms(connection),
                )
    finally:
        connection.close()


@pytest.mark.scenario("JOB-003")
@pytest.mark.scenario("JOB-004")
def test_JOB_003_004_crash_after_atomic_effect_replays_exact_result_once(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    job = _enqueue(factory, "commit-crash")
    fence_file = isolated_knowledge_layout.root / "commit-fence.json"
    crashed = _spawn(
        isolated_knowledge_layout,
        "run_crash",
        {
            "owner": "worker-commit",
            "lease_ms": 10_000,
            "result_file": str(fence_file),
        },
    )
    _communicate_json(crashed, expected_codes=(74,))
    fence = json.loads(fence_file.read_text(encoding="utf-8"))["claim"]

    replay = _spawn(
        isolated_knowledge_layout,
        "replay",
        {
            "job_id": fence["job_id"],
            "claim_owner": fence["claim_owner"],
            "attempt_count": fence["attempt_count"],
            "result": {"effect_key": job.effect_key, "schema_version": 1},
        },
    )
    replayed = _communicate_json(replay)
    assert replayed == {
        "job_state": "succeeded",
        "replayed": True,
        "result": {"effect_key": job.effect_key, "schema_version": 1},
    }

    connection = factory.open(read_only=True)
    try:
        assert connection.execute(
            "SELECT count(*) FROM outbox_event WHERE logical_key=?",
            (f"job-effect:{job.effect_key}",),
        ).fetchone()[0] == 1
        stored = connection.execute(
            "SELECT state,result_json,attempt_count FROM durable_job WHERE id=?",
            (job.id,),
        ).fetchone()
        assert tuple(stored) == (
            "succeeded",
            json.dumps(
                {"effect_key": job.effect_key, "schema_version": 1},
                sort_keys=True,
                separators=(",", ":"),
            ),
            1,
        )
    finally:
        connection.close()


@pytest.mark.scenario("JOB-001")
@pytest.mark.scenario("JOB-002")
@pytest.mark.scenario("JOB-003")
@pytest.mark.scenario("JOB-004")
def test_fixed_seed_twenty_worker_kills_converge_exactly_once(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    modes = ["claim_exit", "run_crash"] * 10
    random.Random(0x5077).shuffle(modes)
    expected_attempts: dict[str, int] = {}

    for index, mode in enumerate(modes):
        label = f"kill-profile-{index:02d}"
        job = _enqueue(factory, label, max_attempts=3)
        result_file = isolated_knowledge_layout.root / f"{label}.json"
        lease_ms = 5 if mode == "claim_exit" else 10_000
        crashed = _spawn(
            isolated_knowledge_layout,
            mode,
            {
                "owner": f"killed-worker-{index:02d}",
                "lease_ms": lease_ms,
                "result_file": str(result_file),
            },
        )
        expected_exit = 73 if mode == "claim_exit" else 74
        _communicate_json(crashed, expected_codes=(expected_exit,))
        fence = json.loads(result_file.read_text(encoding="utf-8"))["claim"]
        assert fence["job_id"] == job.id

        if mode == "claim_exit":
            _wait_until_db_time(factory, int(fence["expires_at"]))
            replacement = _kernel(factory)
            recovery, claim = replacement.recover_and_claim(
                claim_owner=f"replacement-{index:02d}", lease_ms=10_000
            )
            assert recovery.retried_ids == (job.id,)
            assert claim is not None and claim.job.id == job.id
            completed = replacement.run_claim(claim)
            assert completed.job.state == "succeeded"
            expected_attempts[job.id] = 2
        else:
            replayed = _kernel(factory).commit_prepared(
                ClaimFence(
                    job.id,
                    str(fence["claim_owner"]),
                    int(fence["attempt_count"]),
                ),
                PreparedJobEffect(
                    result={"effect_key": job.effect_key, "schema_version": 1}
                ),
            )
            assert replayed.job.state == "succeeded"
            assert replayed.replayed is True
            expected_attempts[job.id] = 1

    connection = factory.open(read_only=True)
    try:
        rows = connection.execute(
            "SELECT id,state,attempt_count FROM durable_job ORDER BY id"
        ).fetchall()
        assert len(rows) == 20
        assert all(row["state"] == "succeeded" for row in rows)
        assert {
            row["id"]: int(row["attempt_count"]) for row in rows
        } == expected_attempts
        effects = connection.execute(
            """SELECT logical_key,count(*) AS n
                 FROM outbox_event
                WHERE logical_key LIKE 'job-effect:fixture-effect:kill-profile-%'
                GROUP BY logical_key"""
        ).fetchall()
        assert len(effects) == 20
        assert all(int(row["n"]) == 1 for row in effects)
        assert connection.execute(
            "SELECT count(*) FROM durable_job WHERE state<>'succeeded'"
        ).fetchone()[0] == 0
    finally:
        connection.close()


@pytest.mark.scenario("JOB-005")
def test_JOB_005_two_processes_claim_same_job_at_most_once(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    job = _enqueue(factory, "claim-race")
    start = isolated_knowledge_layout.root / "claim-race-start"
    ready = (
        isolated_knowledge_layout.root / "claim-race-ready-a",
        isolated_knowledge_layout.root / "claim-race-ready-b",
    )
    processes = tuple(
        _spawn(
            isolated_knowledge_layout,
            "race_claim",
            {
                "owner": owner,
                "lease_ms": 10_000,
                "ready": str(ready_path),
                "start": str(start),
            },
        )
        for owner, ready_path in zip(("gpu-a", "gpu-b"), ready, strict=True)
    )
    _wait_paths(ready, processes)
    start.touch()
    results = tuple(_communicate_json(process) for process in processes)
    claims = [result["claim"] for result in results if result["claim"] is not None]
    assert len(claims) == 1
    assert claims[0]["job_id"] == job.id
    assert claims[0]["attempt_count"] == 1

    connection = factory.open(read_only=True)
    try:
        row = connection.execute(
            "SELECT state,attempt_count,claim_owner FROM durable_job WHERE id=?",
            (job.id,),
        ).fetchone()
        assert row[0] == "claimed" and row[1] == 1
        assert row[2] in ("gpu-a", "gpu-b")
    finally:
        connection.close()


@pytest.mark.scenario("JOB-006")
def test_JOB_006_disable_between_prepare_and_commit_rejects_domain_effect(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    space_id = _uuid("space:worker-user")
    connection = factory.open()
    try:
        with transaction(connection):
            UserRepository().import_legacy(
                connection,
                LegacyUserRecord(
                    id="workeruser",
                    display_name="Worker User",
                    password_hash="scrypt:p1.9:workeruser",
                    system_role="user",
                    account_status="active",
                    created_at_ms=100,
                    updated_at_ms=100,
                ),
            )
            connection.execute(
                """INSERT INTO knowledge_space(
                     id,space_kind,owner_user_id,owner_org_unit_id,status,
                     write_enabled,generation,created_at,updated_at,row_version
                   ) VALUES (?,'personal','workeruser',NULL,'active',1,0,200,200,1)""",
                (space_id,),
            )
    finally:
        connection.close()
    job = _enqueue(
        factory,
        "authorization-recheck",
        actor_kind="user",
        actor_user_id="workeruser",
        space_id=space_id,
        scope_epoch=1,
        cancellable=False,
    )

    prepared = isolated_knowledge_layout.root / "user-job-prepared"
    commit = isolated_knowledge_layout.root / "user-job-commit"
    worker = _spawn(
        isolated_knowledge_layout,
        "user_commit_wait",
        {
            "actor_kind": "user",
            "owner": "worker-user-auth",
            "lease_ms": 30_000,
            "prepared": str(prepared),
            "commit": str(commit),
        },
    )
    _wait_paths((prepared,), (worker,))

    connection = factory.open()
    try:
        with transaction(connection):
            now_ms = db_now_ms(connection)
            connection.execute(
                """UPDATE app_user
                      SET account_status='disabled',disabled_at=?,scope_epoch=2,
                          updated_at=?,row_version=2
                    WHERE id='workeruser' AND account_status='active'
                      AND scope_epoch=1 AND row_version=1""",
                (now_ms, now_ms),
            )
    finally:
        connection.close()
    commit.touch()
    result = _communicate_json(worker)
    assert result["job_state"] == "failed"
    assert result["error"]["code"] == "JOB_AUTHORIZATION_REJECTED"

    connection = factory.open(read_only=True)
    try:
        assert connection.execute(
            "SELECT count(*) FROM outbox_event WHERE logical_key=?",
            (f"job-effect:{job.effect_key}",),
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT account_status,scope_epoch FROM app_user WHERE id='workeruser'"
        ).fetchone()[:] == ("disabled", 2)
    finally:
        connection.close()


def test_cancel_requested_expired_claim_converges_to_terminal_cancelled(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    job = _enqueue(factory, "cancel-recovery", cancellable=True)
    repository = JobRepository()
    connection = factory.open()
    try:
        with transaction(connection):
            now_ms = db_now_ms(connection)
            claim = repository.claim_next(
                connection,
                handler_keys=(_HANDLER,),
                claim_owner="worker-cancelled",
                lease_ms=1,
                now_ms=now_ms,
            )
            assert claim is not None
            repository.start(connection, claim.fence, now_ms=now_ms)
            repository.request_cancel(connection, job_id=job.id, now_ms=now_ms)
            expiry = claim.job.claim_expires_at_ms
            assert expiry is not None
        _wait_until_db_time(factory, expiry)
        with transaction(connection):
            recovery = repository.recover_expired(
                connection,
                now_ms=db_now_ms(connection),
                error={"code": "JOB_CLAIM_EXPIRED"},
            )
            assert recovery.cancelled_ids == (job.id,)
            recovered = repository.require(connection, job.id)
            assert recovered.state == "cancelled"
            assert recovered.cancel_requested is True
    finally:
        connection.close()

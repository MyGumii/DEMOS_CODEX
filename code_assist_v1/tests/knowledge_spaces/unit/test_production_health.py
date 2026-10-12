from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import UUID, uuid5

import pytest

from code_assist_v1.knowledge_core.db import (
    ConnectionFactory,
    database_runtime_metrics,
    db_now_ms,
    observe_sqlite_health,
    transaction,
)
from code_assist_v1.knowledge_core.migrations import (
    CANONICAL_MIGRATION_CATALOG,
    MigrationStorageLayout,
    SingleMigrator,
)
from code_assist_v1.knowledge_core.production_health import (
    MAX_VERIFIED_BACKUP_AGE_MS,
    PRODUCTION_HEALTH_SCHEMA,
    ProductionHealthService,
    build_production_health_snapshot,
)
from code_assist_v1.knowledge_core.repositories.backup import BackupRepository
from code_assist_v1.knowledge_core.repositories.jobs import (
    HandlerKey,
    JobEnqueueDraft,
    JobRepository,
)
from code_assist_v1.knowledge_core.repositories.outbox import (
    OutboxEventDraft,
    OutboxRepository,
)
from code_assist_v1.knowledge_core.repositories.spaces import SpaceRepository
from code_assist_v1.knowledge_core.repositories.users import (
    LegacyUserRecord,
    UserRepository,
)


pytestmark = pytest.mark.unit
_NAMESPACE = UUID("8181f6f5-1828-4f57-b777-320906e99a78")


def _uuid(label: str) -> str:
    return str(uuid5(_NAMESPACE, label))


def _factory(layout) -> ConnectionFactory:
    factory = ConnectionFactory._for_isolated_tests(
        layout.core_db_path, CANONICAL_MIGRATION_CATALOG
    )
    SingleMigrator(
        factory,
        app_version="p7.6-health-test",
        storage_layout=MigrationStorageLayout(
            data_root=layout.root,
            database=layout.core_db_path,
            blob_root=layout.blobs_dir,
            projection_root=layout.projections_dir,
        ),
    ).run()
    return factory


def _environment(**extra: str) -> dict[str, str]:
    value = {
        "KNOWLEDGE_CORE_ENABLED": "0",
        "KNOWLEDGE_CORE_HIGHER_SPACES": "0",
        "KNOWLEDGE_CORE_PART_READS": "0",
        "KNOWLEDGE_CORE_PART_WRITES": "0",
        "KNOWLEDGE_CORE_PERSONAL_WRITES": "0",
        "KNOWLEDGE_CORE_PROJECTIONS": "0",
        "KNOWLEDGE_CORE_READS": "0",
    }
    value.update(extra)
    return value


def _verified_backup(factory: ConnectionFactory, *, age_ms: int) -> int:
    repository = BackupRepository()
    connection = factory.open()
    try:
        with transaction(connection):
            now_ms = db_now_ms(connection)
            completed_at = max(0, now_ms - age_ms)
            created_at = max(0, completed_at - 100)
            repository.begin(
                connection,
                backup_run_id=_uuid(f"backup:{age_ms}"),
                created_at_ms=created_at,
            )
            repository.begin_copying(
                connection,
                backup_run_id=_uuid(f"backup:{age_ms}"),
                snapshot_db_sha256="a" * 64,
                snapshot_audit_high_water=0,
                blobs=(),
            )
            repository.mark_verified(
                connection,
                backup_run_id=_uuid(f"backup:{age_ms}"),
                manifest_sha256="b" * 64,
                completed_at_ms=completed_at,
            )
            return now_ms
    finally:
        connection.close()


def _snapshot(factory, layout, *, environ=None, free_bytes=10**15):
    return build_production_health_snapshot(
        factory,
        blob_root=layout.blobs_dir,
        config_sha256="c" * 64,
        service_id="CodeAssist-Web",
        environ=_environment() if environ is None else environ,
        free_space_probe=lambda _path: free_bytes,
        volume_probe=lambda path: {
            "eligible": True,
            "exists": True,
            "reparse": {"present": False},
            "resolved_path": str(path),
            "volume": {"drive_type_code": 3, "filesystem": "NTFS"},
        },
        allow_test_runtime_override=True,
    )


def _file_digest(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def _tree_digest(root: Path) -> tuple[tuple[str, str], ...]:
    return tuple(
        (
            path.relative_to(root).as_posix(),
            hashlib.sha256(path.read_bytes()).hexdigest(),
        )
        for path in sorted(root.rglob("*"))
        if path.is_file()
    )


def test_sqlite_health_uses_one_validated_query_only_boundary(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    facts = observe_sqlite_health(factory)
    assert facts.schema_version == CANONICAL_MIGRATION_CATALOG.latest_version
    assert facts.quick_check_ok is True
    assert facts.foreign_key_violation_count == 0
    assert facts.fts5_executable is True
    assert "ENABLE_FTS5" in facts.compile_options
    assert facts.pragmas["query_only"] == 1
    assert facts.pragmas["journal_mode"] == "wal"

    counters = database_runtime_metrics()
    assert set(counters) == {
        "application_retry_count",
        "execute_count",
        "sqlite_busy_count",
        "sqlite_locked_count",
    }
    assert counters["execute_count"] > 0
    assert counters["application_retry_count"] == 0
    assert counters["sqlite_busy_count"] >= 0
    assert counters["sqlite_locked_count"] >= 0


def test_fresh_database_is_not_ready_until_a_verified_backup_exists(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    report, status = _snapshot(factory, isolated_knowledge_layout)
    assert status == 503
    assert report["report_schema"] == PRODUCTION_HEALTH_SCHEMA
    assert report["gate"] == {
        "reason_codes": ["verified_backup_missing"],
        "status": "not_ready",
    }
    assert report["backup"]["age_ms"] is None
    assert report["jobs"]["unrecoverable_count"] == 0
    assert report["outbox"]["state_counts"]["dead"] == 0
    assert report["leases"] == {
        "active_count": 0,
        "released_count": 0,
        "stale_count": 0,
    }


def test_recent_verified_backup_produces_a_complete_secret_free_ready_snapshot(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _verified_backup(factory, age_ms=1_000)
    environment = _environment(
        OPENAI_API_KEY="health-secret-sentinel",
        OUTLOOK_CLIENT_SECRET="second-secret-sentinel",
    )
    report, status = _snapshot(
        factory, isolated_knowledge_layout, environ=environment
    )
    assert status == 200
    assert report["gate"] == {"reason_codes": [], "status": "ready"}
    assert report["build"]["schema_migration"]["version"] == (
        CANONICAL_MIGRATION_CATALOG.latest_version
    )
    assert report["sqlite"]["quick_check_ok"] is True
    assert report["sqlite"]["foreign_key_violation_count"] == 0
    assert report["storage"]["migration_space_multiplier"] == 3
    encoded = json.dumps(report, sort_keys=True)
    assert "health-secret-sentinel" not in encoded
    assert "second-secret-sentinel" not in encoded
    assert str(isolated_knowledge_layout.root) not in encoded


def test_health_collection_is_read_only_for_database_wal_and_blob_tree(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _verified_backup(factory, age_ms=1_000)
    database = Path(factory.database_path)
    wal = Path(str(database) + "-wal")
    before = (
        _file_digest(database),
        _file_digest(wal),
        _tree_digest(isolated_knowledge_layout.blobs_dir),
    )
    report, status = _snapshot(factory, isolated_knowledge_layout)
    after = (
        _file_digest(database),
        _file_digest(wal),
        _tree_digest(isolated_knowledge_layout.blobs_dir),
    )
    assert status == 200
    assert report["gate"]["status"] == "ready"
    assert after == before


@pytest.mark.parametrize(
    ("ineligible_role", "expected_reason"),
    [
        ("database", "database_storage_ineligible"),
        ("database_parent", "database_parent_storage_ineligible"),
        ("blob", "blob_storage_ineligible"),
    ],
)
def test_storage_is_revalidated_before_database_or_file_observation(
    isolated_knowledge_layout,
    monkeypatch: pytest.MonkeyPatch,
    ineligible_role: str,
    expected_reason: str,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    paths = {
        "blob": str(isolated_knowledge_layout.blobs_dir),
        "database": str(factory.database_path),
        "database_parent": str(factory.database_path.parent),
    }

    def probe(path):
        role = next(name for name, value in paths.items() if str(path) == value)
        return {
            "eligible": True,
            "exists": True,
            "reparse": {"present": role == ineligible_role},
            "resolved_path": str(path),
            "volume": {
                "drive_type_code": 4 if role == ineligible_role else 3,
                "filesystem": "NTFS",
            },
        }

    def forbidden(*_args, **_kwargs):
        raise AssertionError("storage observations must stop before an unsafe path")

    monkeypatch.setattr(
        "code_assist_v1.knowledge_core.production_health.observe_sqlite_health",
        forbidden,
    )
    report, status = build_production_health_snapshot(
        factory,
        blob_root=isolated_knowledge_layout.blobs_dir,
        config_sha256="c" * 64,
        service_id="CodeAssist-Web",
        environ=_environment(),
        free_space_probe=forbidden,
        file_size_probe=forbidden,
        volume_probe=probe,
        allow_test_runtime_override=True,
    )
    assert status == 503
    assert report["gate"] == {
        "reason_codes": [expected_reason],
        "status": "not_ready",
    }
    assert report["storage"]["eligibility"] == {
        "blob": ineligible_role != "blob",
        "database": ineligible_role != "database",
        "database_parent": ineligible_role != "database_parent",
    }


def test_stale_backup_invalid_flags_failpoint_and_low_disk_are_closed_reasons(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _verified_backup(factory, age_ms=MAX_VERIFIED_BACKUP_AGE_MS + 1)
    environment = _environment(
        KNOWLEDGE_CORE_READS="maybe",
        CODE_ASSIST_FAILPOINT_AFTER_COMMIT="1",
    )
    report, status = _snapshot(
        factory,
        isolated_knowledge_layout,
        environ=environment,
        free_bytes=0,
    )
    assert status == 503
    assert report["gate"]["reason_codes"] == [
        "blob_disk_capacity_low",
        "database_disk_capacity_low",
        "failpoint_configuration_present",
        "feature_flags_invalid",
        "verified_backup_stale",
    ]
    assert report["feature_flags"]["KNOWLEDGE_CORE_READS"] == -1


def test_dead_outbox_and_failed_job_are_aggregated_without_error_payloads(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _verified_backup(factory, age_ms=1_000)
    connection = factory.open()
    outbox = OutboxRepository()
    jobs = JobRepository()
    try:
        with transaction(connection):
            now_ms = db_now_ms(connection)
            event = outbox.append(
                connection,
                OutboxEventDraft(
                    logical_key="health:dead-event",
                    space_id=None,
                    aggregate_type="health",
                    aggregate_id="aggregate",
                    aggregate_version=1,
                    event_type="health.fixture",
                    payload={"safe": True},
                    available_at_ms=now_ms,
                ),
            )
            claim = outbox.claim_next(
                connection,
                claim_owner="health-worker",
                lease_ms=10_000,
                now_ms=now_ms,
                max_attempts=1,
            )
            assert claim is not None and claim.event.id == event.id
            outbox.retry_or_dead(
                connection,
                claim.fence,
                error={"secret": "outbox-error-sentinel"},
                delay_ms=0,
                max_attempts=1,
                now_ms=now_ms,
            )

            queued = jobs.enqueue(
                connection,
                JobEnqueueDraft(
                    job_id=_uuid("health-job"),
                    dedup_id=None,
                    operation_id=None,
                    actor_kind="system",
                    actor_user_id=None,
                    space_id=None,
                    job_type="health.fixture",
                    handler_version="v1",
                    effect_key="health:fixture",
                    input_ref={"safe": True},
                    expected_state={},
                    authorization_snapshot={
                        "schema_version": 1,
                        "actor_kind": "system",
                    },
                    authorization_scope_epoch=None,
                    authorization_part_id=None,
                    authorization_capabilities=(),
                    max_attempts=2,
                    cancellable=False,
                    available_at_ms=now_ms,
                ),
            )
            claimed = jobs.claim_next(
                connection,
                handler_keys=(HandlerKey("health.fixture", "v1"),),
                claim_owner="health-worker",
                lease_ms=10_000,
                now_ms=now_ms,
            )
            assert claimed is not None and claimed.job.id == queued.job.id
            jobs.start(connection, claimed.fence, now_ms=now_ms)
            jobs.fail(
                connection,
                claimed.fence,
                error={"secret": "job-error-sentinel"},
                now_ms=now_ms,
            )
    finally:
        connection.close()

    report, status = _snapshot(factory, isolated_knowledge_layout)
    assert status == 503
    assert report["gate"]["reason_codes"] == [
        "dead_outbox_present",
        "unrecoverable_job_present",
    ]
    assert report["outbox"]["state_counts"]["dead"] == 1
    assert report["outbox"]["attempt_count"] == 1
    assert report["jobs"]["state_counts"]["failed"] == 1
    assert report["jobs"]["unrecoverable_count"] == 1
    encoded = json.dumps(report, sort_keys=True)
    assert "outbox-error-sentinel" not in encoded
    assert "job-error-sentinel" not in encoded


def test_health_service_converts_any_observer_failure_to_a_stable_503(
    isolated_knowledge_layout,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = _factory(isolated_knowledge_layout)

    def fail(*_args, **_kwargs):
        raise RuntimeError("private-health-error-sentinel")

    monkeypatch.setattr(
        "code_assist_v1.knowledge_core.production_health.build_production_health_snapshot",
        fail,
    )
    service = ProductionHealthService(
        factory,
        blob_root=isolated_knowledge_layout.blobs_dir,
        config_sha256="d" * 64,
        service_id="CodeAssist-Notion",
        environ=_environment(),
    )
    report, status = service()
    assert status == 503
    assert report == {
        "gate": {
            "reason_codes": ["health_observation_failed"],
            "status": "not_ready",
        },
        "report_schema": PRODUCTION_HEALTH_SCHEMA,
        "schema_version": 1,
        "service_id": "CodeAssist-Notion",
    }
    assert "private-health-error-sentinel" not in json.dumps(report)


def test_health_service_cache_is_singleflight_and_caller_mutation_safe(
    isolated_knowledge_layout,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    calls: list[str] = []
    expected = {
        "gate": {"reason_codes": [], "status": "ready"},
        "report_schema": PRODUCTION_HEALTH_SCHEMA,
        "schema_version": 1,
        "service_id": "CodeAssist-Web",
    }

    def collect(*_args, **_kwargs):
        calls.append("collect")
        return dict(expected), 200

    monkeypatch.setattr(
        "code_assist_v1.knowledge_core.production_health.build_production_health_snapshot",
        collect,
    )
    service = ProductionHealthService(
        factory,
        blob_root=isolated_knowledge_layout.blobs_dir,
        config_sha256="d" * 64,
        service_id="CodeAssist-Web",
        environ=_environment(),
    )
    first, first_status = service()
    first["service_id"] = "tampered"
    second, second_status = service()
    assert calls == ["collect"]
    assert first_status == second_status == 200
    assert second == expected


def test_leases_projection_lag_and_referenced_blob_capacity_are_exact_aggregates(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _verified_backup(factory, age_ms=1_000)
    connection = factory.open()
    user_id = "health-admin"
    space_id = _uuid("health-space")
    blob_sha = "f" * 64
    raw_sha = blob_sha
    try:
        with transaction(connection):
            now_ms = db_now_ms(connection)
            UserRepository().import_legacy(
                connection,
                LegacyUserRecord(
                    id=user_id,
                    display_name="Health Admin",
                    password_hash="scrypt:health:sentinel-password",
                    system_role="admin",
                    account_status="active",
                    created_at_ms=100,
                    updated_at_ms=100,
                ),
            )
            SpaceRepository().ensure_personal(
                connection,
                space_id=space_id,
                user_id=user_id,
                created_at_ms=200,
            )
            for index, state in enumerate(("active", "stale"), start=1):
                item_id = _uuid(f"health-item:{index}")
                revision_id = _uuid(f"health-revision:{index}")
                connection.execute(
                    """INSERT INTO knowledge_item(
                           id,space_id,lineage_group_id,lifecycle_status,
                           curation_status,current_revision_id,
                           last_curation_transition_id,latest_deactivation_episode_id,
                           redirect_item_id,created_by,updated_by,created_at,
                           updated_at,row_version
                       ) VALUES (?,?,NULL,'active','provisional',?,NULL,NULL,NULL,
                                 ?,?,?,?,1)""",
                    (item_id, space_id, revision_id, user_id, user_id, 300, 300),
                )
                connection.execute(
                    """INSERT INTO knowledge_revision(
                           id,item_id,revision_no,parent_revision_id,base_revision_id,
                           title,raw_content_sha256,normalized_content_sha256,
                           content_size_bytes,normalizer_version,metadata_json,
                           change_kind,change_reason,created_by,created_at
                       ) VALUES (?,?,1,NULL,NULL,?,'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
                                 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',
                                 0,'markdown_lf_v1','{}','initial','health fixture',?,300)""",
                    (revision_id, item_id, f"Health {index}", user_id),
                )
                expires_at = now_ms + 10_000 if state == "active" else now_ms
                connection.execute(
                    """UPDATE item_lease SET holder_user_id=?,editor_session_id=?,
                              token_hmac=?,hmac_key_id='health-key',fencing_generation=1,
                              base_item_row_version=1,base_revision_id=?,acquired_at=?,
                              heartbeat_at=?,expires_at=?,absolute_expires_at=?
                          WHERE item_id=?""",
                    (
                        user_id,
                        _uuid(f"health-session:{index}"),
                        bytes([index]) * 32,
                        revision_id,
                        now_ms - 2_000,
                        now_ms - 1_000,
                        expires_at,
                        now_ms + 20_000,
                        item_id,
                    ),
                )
            connection.execute(
                """INSERT INTO projection_state(
                       projection_name,space_id,desired_generation,built_generation,
                       checksum_sha256,status,last_error_json,updated_at,built_at,
                       row_version
                   ) VALUES ('markdown',?,5,2,NULL,'error',?, ?,NULL,1)""",
                (
                    space_id,
                    '{"secret":"projection-error-sentinel"}',
                    now_ms - 5_000,
                ),
            )
            connection.execute(
                """INSERT INTO blob_object(
                       sha256,size_bytes,media_type,relative_path,
                       verification_status,created_at,verified_at
                   ) VALUES (?,123,'application/octet-stream',?,'verified',400,400)""",
                (blob_sha, f"{blob_sha[:2]}/{blob_sha[2:4]}/{blob_sha}"),
            )
            artifact_id = _uuid("health-artifact")
            connection.execute(
                """INSERT INTO source_artifact(
                       id,origin_type,origin_key,origin_revision_key,raw_sha256,
                       raw_size_bytes,media_type,original_filename,
                       body_retention_status,body_retention_reason,body_purged_at,
                       orphan_cleanup_eligible_at,publisher_user_id,
                       source_owner_snapshot_json,metadata_json,
                       metadata_schema_version,created_at
                   ) VALUES (?,'manual','health',NULL,?,123,
                             'application/octet-stream','health.bin','present',
                             NULL,NULL,2592000400,?,'{}','{}',1,400)""",
                (artifact_id, raw_sha, user_id),
            )
            artifact_blob_id = _uuid("health-artifact-blob")
            connection.execute(
                """INSERT INTO artifact_blob(
                       id,artifact_id,content_sha256,size_bytes,role,logical_name,
                       media_type,ordinal,body_retention_status,created_at
                   ) VALUES (?,?,?,123,'raw','health.bin',
                             'application/octet-stream',0,'present',400)""",
                (artifact_blob_id, artifact_id, blob_sha),
            )
            connection.execute(
                """INSERT INTO artifact_blob_body(
                       artifact_blob_id,content_sha256,created_at
                   ) VALUES (?,?,400)""",
                (artifact_blob_id, blob_sha),
            )
    finally:
        connection.close()

    report, status = _snapshot(factory, isolated_knowledge_layout)
    assert status == 503
    assert report["gate"]["reason_codes"] == ["projection_error_present"]
    assert report["leases"] == {
        "active_count": 1,
        "released_count": 0,
        "stale_count": 1,
    }
    assert report["projection"] == {
        "built_generation": 2,
        "desired_generation": 5,
        "error_count": 1,
        "lag_generation": 3,
        "lag_generation_max": 3,
        "lagging_count": 1,
        "oldest_lag_age_ms": pytest.approx(5_000, abs=1_000),
        "row_count": 1,
    }
    assert report["storage"]["blob"]["referenced"] == {
        "count": 1,
        "size_bytes": 123,
        "unverified_count": 0,
    }
    assert "projection-error-sentinel" not in json.dumps(report, sort_keys=True)
    assert "sentinel-password" not in json.dumps(report, sort_keys=True)


@pytest.mark.parametrize("application", ["code", "notion"])
def test_both_web_hosts_return_the_production_health_provider_status(
    isolated_knowledge_layout,
    monkeypatch: pytest.MonkeyPatch,
    application: str,
) -> None:
    del isolated_knowledge_layout
    monkeypatch.setenv("CODE_ASSIST_PROCESS_ROLE", "web")
    monkeypatch.setenv("CODE_ASSIST_STANDALONE", "0")
    payload = {
        "gate": {
            "reason_codes": ["verified_backup_stale"],
            "status": "not_ready",
        },
        "report_schema": PRODUCTION_HEALTH_SCHEMA,
        "schema_version": 1,
        "service_id": (
            "CodeAssist-Web" if application == "code" else "CodeAssist-Notion"
        ),
    }

    if application == "code":
        from code_assist_v1.app_code import create_app
    else:
        from code_assist_v1.app_notion import create_app

    app = create_app(production_health_provider=lambda: (payload, 503))
    response = app.test_client().get("/healthz")
    assert response.status_code == 503
    assert response.get_json() == payload
    assert response.headers["Cache-Control"] == (
        "no-store, no-cache, must-revalidate, max-age=0"
    )


def test_fallback_health_never_exposes_token_source_or_filesystem_path(
    isolated_knowledge_layout,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del isolated_knowledge_layout
    monkeypatch.setenv("CODE_ASSIST_PROCESS_ROLE", "web")
    monkeypatch.setenv("CODE_ASSIST_STANDALONE", "0")
    from code_assist_v1.app_code import create_app

    payload = create_app().test_client().get("/healthz").get_json()
    assert payload["mode"] == "production"
    assert "token_file" not in payload
    assert "token_source" not in payload

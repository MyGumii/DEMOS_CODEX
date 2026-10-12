from __future__ import annotations

from dataclasses import replace
from uuid import UUID, uuid5

import pytest

from code_assist_v1.knowledge_core.db import ConnectionFactory, transaction
from code_assist_v1.knowledge_core.migrations import (
    CANONICAL_MIGRATION_CATALOG,
    MigrationStorageLayout,
    SingleMigrator,
)
from code_assist_v1.knowledge_core.repositories.audit import AuditRepository
from code_assist_v1.knowledge_core.repositories.organization import (
    OrganizationRepository,
    RESPONSIBILITY_ROOT_ID,
)
from code_assist_v1.knowledge_core.repositories.outbox import OutboxRepository
from code_assist_v1.knowledge_core.repositories.spaces import SpaceRepository
from code_assist_v1.knowledge_core.repositories.users import (
    LegacyUserRecord,
    UserRepository,
)
from code_assist_v1.knowledge_core.services.organization import OrganizationService
from code_assist_v1.knowledge_core.services.spaces import (
    ProvisionPhase1SpacesCommand,
    SpaceProvisioningFailpointTriggered,
    SpaceProvisioningIdempotencyError,
    SpaceProvisioningScopeStaleError,
    SpaceProvisioningService,
    space_id_for_owner,
)


pytestmark = pytest.mark.unit
_NAMESPACE = UUID("5a997457-bf0a-47a0-b7d4-6a41314aa0b6")


def _uuid(label: str) -> str:
    return str(uuid5(_NAMESPACE, label))


def _factory(layout) -> ConnectionFactory:
    factory = ConnectionFactory._for_isolated_tests(
        layout.core_db_path, CANONICAL_MIGRATION_CATALOG
    )
    SingleMigrator(
        factory,
        app_version="p1.6-space-service-test",
        storage_layout=MigrationStorageLayout(
            data_root=layout.root,
            database=layout.core_db_path,
            blob_root=layout.blobs_dir,
            projection_root=layout.projections_dir,
        ),
    ).run()
    return factory


def _user(
    user_id: str, *, role: str = "user", disabled: bool = False
) -> LegacyUserRecord:
    return LegacyUserRecord(
        id=user_id,
        display_name=user_id.title(),
        password_hash=f"scrypt:p1.6:{user_id}",
        system_role=role,  # type: ignore[arg-type]
        account_status="disabled" if disabled else "active",
        created_at_ms=100,
        updated_at_ms=100,
        disabled_at_ms=100 if disabled else None,
    )


def _seed_users(factory: ConnectionFactory) -> None:
    connection = factory.open()
    try:
        with transaction(connection):
            UserRepository().import_legacy_many(
                connection,
                (
                    _user("admin", role="admin"),
                    _user("member"),
                    _user("disabled", disabled=True),
                ),
            )
    finally:
        connection.close()


def _seed_parts(factory: ConnectionFactory) -> tuple[str, str, str]:
    repository = OrganizationRepository()
    connection = factory.open()
    try:
        with transaction(connection):
            team = repository.create_unit(
                connection,
                unit_id=_uuid("team"),
                kind="team",
                parent_id=RESPONSIBILITY_ROOT_ID,
                name="Provisioning Team",
                actor_user_id="admin",
                now_ms=1000,
            )
            part_a = repository.create_unit(
                connection,
                unit_id=_uuid("part:a"),
                kind="part",
                parent_id=team.id,
                name="Active Part A",
                actor_user_id="admin",
                now_ms=1001,
            )
            part_b = repository.create_unit(
                connection,
                unit_id=_uuid("part:b"),
                kind="part",
                parent_id=team.id,
                name="Active Part B",
                actor_user_id="admin",
                now_ms=1002,
            )
            inactive = repository.create_unit(
                connection,
                unit_id=_uuid("part:inactive"),
                kind="part",
                parent_id=team.id,
                name="Inactive Part",
                actor_user_id="admin",
                now_ms=1003,
            )
            repository.set_unit_status(
                connection,
                unit_id=inactive.id,
                expected_row_version=inactive.row_version,
                status="inactive",
                actor_user_id="admin",
                updated_at_ms=1004,
            )
        return part_a.id, part_b.id, inactive.id
    finally:
        connection.close()


def _command(label: str = "initial", *, epoch: int = 1):
    return ProvisionPhase1SpacesCommand(
        actor_user_id="admin",
        actor_scope_epoch=epoch,
        operation_id=_uuid(f"operation:{label}"),
        request_id=_uuid(f"request:{label}"),
        reason="Provision every missing phase-1 knowledge space",
        request_meta={"remote_addr": "127.0.0.1"},
    )


@pytest.mark.scenario("ORG-001")
@pytest.mark.scenario("ORG-006")
@pytest.mark.scenario("SCHEMA-001")
@pytest.mark.scenario("SPACE-005")
def test_phase1_provisioning_creates_exact_lower_bound_without_writes_or_higher_traffic(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _seed_users(factory)
    part_a, part_b, inactive_part = _seed_parts(factory)
    result = SpaceProvisioningService(factory).provision_phase1_spaces(_command())

    assert result.status == "created"
    assert result.created_personal_count == 3
    assert result.created_part_count == 2
    assert result.existing_personal_count == result.existing_part_count == 0
    assert result.total_user_count == result.personal_space_count == 3
    assert result.active_part_count == result.active_part_space_count == 2
    assert result.total_part_space_count == 2
    assert result.higher_space_count == 1
    assert len(result.outcomes) == 5
    assert [
        (value.owner_kind, value.owner_id) for value in result.outcomes
    ] == [
        ("personal", "admin"),
        ("personal", "disabled"),
        ("personal", "member"),
        *[("part", part_id) for part_id in sorted((part_a, part_b))],
    ]

    connection = factory.open()
    try:
        spaces = SpaceRepository()
        for outcome in result.outcomes:
            record = spaces.require_by_id(connection, outcome.space_id)
            assert record.status == "active"
            assert record.write_enabled is False
            assert record.generation == 0 and record.row_version == 1
        assert spaces.get_personal(connection, "disabled") is not None
        assert spaces.get_part(connection, inactive_part) is None
        assert spaces.provisioning_report(connection).complete
        assert connection.execute(
            "SELECT count(*) FROM knowledge_space WHERE space_kind IN ('team','responsibility')"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT count(*) FROM audit_event WHERE operation_id=?",
            (result.operation_id,),
        ).fetchone()[0] == 6
        assert connection.execute(
            "SELECT count(*) FROM outbox_event WHERE event_type='knowledge_space.provisioned'"
        ).fetchone()[0] == 5
        assert connection.execute("SELECT count(*) FROM operation_dedup").fetchone()[0] == 1

        tree = OrganizationService(
            factory, preview_hmac_key=b"p1.6 test preview key exactly 32!!"
        ).get_tree("admin", 1, _uuid("request:tree")).as_dict()
        rendered_parts = tree["root"]["children"][0]["children"]
        statuses = {item["id"]: item["space_status"] for item in rendered_parts}
        assert statuses[part_a] == statuses[part_b] == "write_disabled"
        assert statuses[inactive_part] == "not_created"
    finally:
        connection.close()


@pytest.mark.scenario("SCHEMA-001")
def test_exact_retry_and_new_reconcile_preserve_ids_state_and_event_cardinality(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _seed_users(factory)
    part_a, _part_b, _inactive = _seed_parts(factory)
    service = SpaceProvisioningService(factory)
    command = _command("replay")
    first = service.provision_phase1_spaces(command)
    retry_request_id = _uuid("request:replay:new")
    replay = service.provision_phase1_spaces(
        replace(command, request_id=retry_request_id, request_meta={"host": "localhost"})
    )
    assert replay == replace(first, request_id=retry_request_id)

    connection = factory.open()
    try:
        spaces = SpaceRepository()
        part = spaces.get_part(connection, part_a)
        assert part is not None
        with transaction(connection):
            spaces.update_state(
                connection,
                space_id=part.id,
                expected_row_version=part.row_version,
                status="frozen",
                write_enabled=False,
                updated_at_ms=5000,
            )
    finally:
        connection.close()

    second = service.provision_phase1_spaces(_command("second-operation"))
    assert second.status == "no_change"
    assert second.created_count == 0
    assert {value.outcome for value in second.outcomes} == {"existing"}
    connection = factory.open()
    try:
        part = SpaceRepository().get_part(connection, part_a)
        assert part is not None and part.status == "frozen" and part.row_version == 2
        assert connection.execute(
            "SELECT count(*) FROM outbox_event WHERE event_type='knowledge_space.provisioned'"
        ).fetchone()[0] == first.created_count
        assert connection.execute("SELECT count(*) FROM operation_dedup").fetchone()[0] == 2
        assert connection.execute(
            "SELECT count(*) FROM audit_event WHERE action='knowledge_space.provision_phase1'"
        ).fetchone()[0] == 2
    finally:
        connection.close()


def test_exact_retry_preserves_valid_preexisting_non_derived_space_ids(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _seed_users(factory)
    part_a, _part_b, _inactive = _seed_parts(factory)
    personal_space_id = _uuid("preexisting-space:admin")
    part_space_id = _uuid("preexisting-space:part-a")
    connection = factory.open()
    try:
        with transaction(connection):
            spaces = SpaceRepository()
            spaces.ensure_personal(
                connection,
                space_id=personal_space_id,
                user_id="admin",
                created_at_ms=2000,
            )
            spaces.ensure_part(
                connection,
                space_id=part_space_id,
                part_id=part_a,
                created_at_ms=2001,
            )
    finally:
        connection.close()

    service = SpaceProvisioningService(factory)
    command = _command("preexisting-non-derived")
    first = service.provision_phase1_spaces(command)
    outcomes = {
        (outcome.owner_kind, outcome.owner_id): outcome
        for outcome in first.outcomes
    }
    assert outcomes[("personal", "admin")].space_id == personal_space_id
    assert outcomes[("personal", "admin")].outcome == "existing"
    assert outcomes[("part", part_a)].space_id == part_space_id
    assert outcomes[("part", part_a)].outcome == "existing"

    retry_request_id = _uuid("request:preexisting-non-derived:retry")
    replay = service.provision_phase1_spaces(
        replace(command, request_id=retry_request_id)
    )
    assert replay == replace(first, request_id=retry_request_id)


@pytest.mark.parametrize(
    "failpoint",
    (
        "space_provisioning.after_first_insert",
        "space_provisioning.after_domain",
        "space_provisioning.after_audit",
        "space_provisioning.after_outbox",
        "space_provisioning.before_commit",
    ),
)
def test_space_provisioning_failpoints_roll_back_space_audit_outbox_and_dedup(
    isolated_knowledge_layout,
    failpoint: str,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _seed_users(factory)
    _seed_parts(factory)

    def inject(name: str) -> None:
        if name == failpoint:
            raise SpaceProvisioningFailpointTriggered(name)

    with pytest.raises(SpaceProvisioningFailpointTriggered):
        SpaceProvisioningService(factory, failpoint=inject).provision_phase1_spaces(
            _command(f"rollback:{failpoint}")
        )
    connection = factory.open()
    try:
        for table in ("knowledge_space", "audit_event", "outbox_event", "operation_dedup"):
            expected = 1 if table == "knowledge_space" else 0
            assert connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == expected
    finally:
        connection.close()


def test_audit_or_outbox_failure_rolls_back_every_provisioning_effect(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _seed_users(factory)
    _seed_parts(factory)

    class ExplodingAudit(AuditRepository):
        def append(self, connection, draft):  # type: ignore[no-untyped-def]
            del connection, draft
            raise RuntimeError("intentional audit failure")

    with pytest.raises(RuntimeError, match="audit failure"):
        SpaceProvisioningService(factory, audit=ExplodingAudit()).provision_phase1_spaces(
            _command("audit-failure")
        )

    class ExplodingOutbox(OutboxRepository):
        def append(self, connection, draft):  # type: ignore[no-untyped-def]
            del connection, draft
            raise RuntimeError("intentional outbox failure")

    with pytest.raises(RuntimeError, match="outbox failure"):
        SpaceProvisioningService(factory, outbox=ExplodingOutbox()).provision_phase1_spaces(
            _command("outbox-failure")
        )
    connection = factory.open()
    try:
        for table in ("knowledge_space", "audit_event", "outbox_event", "operation_dedup"):
            expected = 1 if table == "knowledge_space" else 0
            assert connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == expected
    finally:
        connection.close()


def test_scope_stale_is_failed_final_and_exact_retry_replays_without_domain_rows(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _seed_users(factory)
    _seed_parts(factory)
    service = SpaceProvisioningService(factory)
    command = _command("stale", epoch=99)
    with pytest.raises(SpaceProvisioningScopeStaleError) as first:
        service.provision_phase1_spaces(command)
    with pytest.raises(SpaceProvisioningScopeStaleError) as replay:
        service.provision_phase1_spaces(
            replace(command, request_id=_uuid("request:stale:retry"))
        )
    assert replay.value.as_error() == first.value.as_error()
    with pytest.raises(SpaceProvisioningIdempotencyError):
        service.provision_phase1_spaces(replace(command, actor_scope_epoch=1))

    connection = factory.open()
    try:
        assert connection.execute("SELECT count(*) FROM knowledge_space").fetchone()[0] == 1
        assert connection.execute(
            "SELECT status FROM operation_dedup"
        ).fetchone()[0] == "failed_final"
        assert connection.execute("SELECT count(*) FROM audit_event").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM outbox_event").fetchone()[0] == 0
    finally:
        connection.close()


def test_stable_owner_ids_and_request_metadata_validation_are_fail_closed(
    isolated_knowledge_layout,
) -> None:
    assert space_id_for_owner("personal", "member") == space_id_for_owner(
        "personal", "member"
    )
    assert space_id_for_owner("personal", "member") != space_id_for_owner(
        "part", _uuid("part:owner")
    )
    factory = _factory(isolated_knowledge_layout)
    _seed_users(factory)
    with pytest.raises(ValueError, match="Sensitive request metadata"):
        SpaceProvisioningService(factory).provision_phase1_spaces(
            replace(_command("secret"), request_meta={"X-CSRF-Token": "secret"})
        )
    connection = factory.open()
    try:
        assert connection.execute("SELECT count(*) FROM knowledge_space").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM operation_dedup").fetchone()[0] == 0
    finally:
        connection.close()

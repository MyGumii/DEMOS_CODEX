from __future__ import annotations

import base64
from dataclasses import dataclass, replace
import hashlib
import hmac
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
from uuid import UUID, uuid5

import pytest

from code_assist_v1.knowledge_core.db import ConnectionFactory, transaction
from code_assist_v1.knowledge_core.domain import FeatureFlags
from code_assist_v1.knowledge_core.migrations import (
    CANONICAL_MIGRATION_CATALOG,
    MigrationStorageLayout,
    SingleMigrator,
)
from code_assist_v1.knowledge_core.repositories.artifacts import StagedVerifiedBlob
from code_assist_v1.knowledge_core.repositories.leases import LeaseRepository
from code_assist_v1.knowledge_core.repositories.organization import (
    OrganizationRepository,
    RESPONSIBILITY_ROOT_ID,
)
from code_assist_v1.knowledge_core.repositories.spaces import SpaceRepository
from code_assist_v1.knowledge_core.repositories.users import (
    LegacyUserRecord,
    UserRepository,
)
from code_assist_v1.knowledge_core.scope import ScopeResolver, SessionPrincipal
from code_assist_v1.knowledge_core.services.editing import (
    AcquireLeaseCommand,
    EditFenceCheck,
    FencedLeaseService,
    ForceReleaseLeaseCommand,
    HeartbeatLeaseCommand,
    LeaseAuthorizationError,
    LeaseHeldError,
    LeaseHmacKeyRing,
    LeaseIdempotencyError,
    LeaseLostError,
    LeaseRevisionConflictError,
    LeaseScopeStaleError,
    LeaseValidationError,
    ReleaseLeaseCommand,
)
from code_assist_v1.knowledge_core.services.publication import (
    CreateManualPreviewCommand,
    PublicationPreviewService,
    PublicationService,
    PublishPreviewCommand,
)
from code_assist_v1.knowledge_core.services.spaces import space_id_for_owner


pytestmark = pytest.mark.unit

_NAMESPACE = UUID("639b6311-d364-4f64-b8fb-2f9524419740")
_KEY_ONE = bytes(range(32))
_KEY_TWO = bytes(reversed(range(32)))
_PACKAGE_ROOT = Path(__file__).resolve().parents[3]


def _uuid(label: str) -> str:
    return str(uuid5(_NAMESPACE, label))


def _token(label: str) -> str:
    raw = hashlib.sha256(label.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _factory(layout) -> ConnectionFactory:
    factory = ConnectionFactory._for_isolated_tests(
        layout.core_db_path,
        CANONICAL_MIGRATION_CATALOG,
    )
    SingleMigrator(
        factory,
        app_version="p5.1-lease-service-test",
        storage_layout=MigrationStorageLayout(
            data_root=layout.root,
            database=layout.core_db_path,
            blob_root=layout.blobs_dir,
            projection_root=layout.projections_dir,
        ),
    ).run()
    return factory


@dataclass(frozen=True)
class _PartFixture:
    factory: ConnectionFactory
    part_id: str
    space_id: str
    item_id: str
    revision_id: str
    epochs: dict[str, int]

    def principal(self, user_id: str) -> SessionPrincipal:
        return SessionPrincipal(user_id, self.epochs[user_id])


class _BlobLease:
    def close(self) -> None:
        return None


@dataclass(frozen=True)
class _Observation:
    blob: StagedVerifiedBlob
    lease: _BlobLease


class _Stager:
    def stage_and_verify(
        self,
        content: bytes,
        *,
        expected_sha256: str,
        media_type: str,
        effective_at_ms: int,
        normalized_content_sha256: str | None,
        normalizer_version: str | None,
    ) -> _Observation:
        del normalized_content_sha256, normalizer_version
        assert hashlib.sha256(content).hexdigest() == expected_sha256
        return _Observation(
            blob=StagedVerifiedBlob(
                sha256=expected_sha256,
                size_bytes=len(content),
                media_type=media_type,
                relative_path=(
                    f"{expected_sha256[:2]}/{expected_sha256[2:4]}/"
                    f"{expected_sha256}"
                ),
                created_at_ms=effective_at_ms,
                verified_at_ms=effective_at_ms,
            ),
            lease=_BlobLease(),
        )


def _write_flags() -> FeatureFlags:
    return FeatureFlags(
        core_enabled=True,
        personal_reads=True,
        personal_writes=True,
        part_reads=True,
        part_writes=True,
    )


def _seed_part_fixture(layout) -> _PartFixture:
    factory = _factory(layout)
    users = UserRepository()
    organization = OrganizationRepository()
    spaces = SpaceRepository()
    team_id = _uuid("team")
    part_id = _uuid("part")
    space_id = _uuid("part-space")
    epochs: dict[str, int] = {}
    connection = factory.open()
    try:
        with transaction(connection):
            for user_id, role in (
                ("editor", "user"),
                ("member", "user"),
                ("leader", "user"),
                ("admin", "admin"),
            ):
                users.import_legacy(
                    connection,
                    LegacyUserRecord(
                        id=user_id,
                        display_name=user_id.title(),
                        password_hash=f"scrypt:p5.1:{user_id}",
                        system_role=role,
                        account_status="active",
                        created_at_ms=100,
                        updated_at_ms=100,
                    ),
                )
                personal = spaces.ensure_personal(
                    connection,
                    space_id=space_id_for_owner("personal", user_id),
                    user_id=user_id,
                    created_at_ms=101,
                )
                spaces.update_state(
                    connection,
                    space_id=personal.space.id,
                    expected_row_version=personal.space.row_version,
                    status="active",
                    write_enabled=True,
                    updated_at_ms=102,
                )
            organization.create_unit(
                connection,
                unit_id=team_id,
                kind="team",
                parent_id=RESPONSIBILITY_ROOT_ID,
                name="Lease Team",
                actor_user_id="leader",
                now_ms=110,
            )
            organization.create_unit(
                connection,
                unit_id=part_id,
                kind="part",
                parent_id=team_id,
                name="Lease Part",
                actor_user_id="leader",
                now_ms=111,
            )
            for index, user_id in enumerate(
                ("editor", "member", "leader", "admin"), 1
            ):
                organization.open_membership(
                    connection,
                    membership_id=_uuid(f"membership:{user_id}"),
                    user_id=user_id,
                    part_id=part_id,
                    assigned_by="leader",
                    reason="P5.1 fixture",
                    valid_from_ms=120 + index,
                )
                before = users.require(connection, user_id)
                after = organization.compare_and_swap_user_part(
                    connection,
                    user_id=user_id,
                    expected_current_part_id=None,
                    target_part_id=part_id,
                    expected_scope_epoch=before.scope_epoch,
                    expected_row_version=before.row_version,
                    updated_at_ms=130 + index,
                )
                epochs[user_id] = after.scope_epoch
            organization.open_part_leader(
                connection,
                assignment_id=_uuid("leader-assignment"),
                user_id="leader",
                part_id=part_id,
                assigned_by="leader",
                reason="P5.1 fixture",
                valid_from_ms=140,
            )
            ensured = spaces.ensure_part(
                connection,
                space_id=space_id,
                part_id=part_id,
                created_at_ms=150,
            )
            spaces.update_state(
                connection,
                space_id=space_id,
                expected_row_version=ensured.space.row_version,
                status="active",
                write_enabled=True,
                updated_at_ms=151,
            )
    finally:
        connection.close()

    resolver = ScopeResolver(_write_flags())
    preview = PublicationPreviewService(factory, resolver, _Stager()).create_manual_preview(
        CreateManualPreviewCommand(
            request_id=_uuid("preview-request"),
            operation_id=_uuid("preview-operation"),
            actor_user_id="editor",
            actor_scope_epoch=epochs["editor"],
            markdown="# Fenced lease fixture\n",
            request_meta={},
        )
    )
    publication = PublicationService(factory, resolver).publish(
        PublishPreviewCommand(
            request_id=_uuid("publication-request"),
            operation_id=_uuid("publication-operation"),
            actor_user_id="editor",
            actor_scope_epoch=epochs["editor"],
            preview_id=preview.preview_id,
            source_snapshot_sha256=preview.source_snapshot_sha256,
            target_mode="part",
            request_meta={},
        )
    )
    assert len(publication.targets) == 1
    target = publication.targets[0]
    return _PartFixture(
        factory=factory,
        part_id=part_id,
        space_id=space_id,
        item_id=str(target["item_id"]),
        revision_id=str(target["resolved_revision_id"]),
        epochs=epochs,
    )


def _service(
    fixture: _PartFixture,
    *,
    keys: LeaseHmacKeyRing | None = None,
    stale_after_ms: int = 300_000,
    failpoint=None,
) -> FencedLeaseService:
    return FencedLeaseService(
        fixture.factory,
        key_ring=keys
        or LeaseHmacKeyRing(active_key_id="lease-v1", keys={"lease-v1": _KEY_ONE}),
        scope_resolver=ScopeResolver(_write_flags()),
        stale_after_ms=stale_after_ms,
        absolute_after_ms=7_200_000,
        failpoint=failpoint,
    )


def _acquire(
    fixture: _PartFixture,
    *,
    user_id: str = "editor",
    label: str = "first",
    service: FencedLeaseService | None = None,
):
    token = _token(label)
    command = AcquireLeaseCommand(
        principal=fixture.principal(user_id),
        space_id=fixture.space_id,
        item_id=fixture.item_id,
        editor_session_id=_uuid(f"session:{user_id}:{label}"),
        operation_id=_uuid(f"acquire:{user_id}:{label}"),
        request_id=_uuid(f"acquire-request:{user_id}:{label}"),
        raw_token=token,
        request_meta={
            "method": "POST",
            "path": "/lease/acquire",
            "X-Knowledge-Lease-Token": token,
            "user_agent": f"sentinel/{token}",
        },
    )
    result = (service or _service(fixture)).acquire(command)
    return command, result


def _heartbeat(
    fixture: _PartFixture,
    acquire_command: AcquireLeaseCommand,
    acquire_result,
    *,
    service: FencedLeaseService | None = None,
):
    return (service or _service(fixture)).heartbeat(
        HeartbeatLeaseCommand(
            principal=acquire_command.principal,
            space_id=fixture.space_id,
            item_id=fixture.item_id,
            editor_session_id=acquire_command.editor_session_id,
            lease_id=acquire_result.lease_id,
            fencing_generation=acquire_result.fencing_generation,
            request_id=_uuid("heartbeat-request"),
            raw_token=acquire_command.raw_token,
        )
    )


def _release_command(
    fixture: _PartFixture,
    acquire_command: AcquireLeaseCommand,
    acquire_result,
    *,
    label: str = "first",
) -> ReleaseLeaseCommand:
    return ReleaseLeaseCommand(
        principal=acquire_command.principal,
        space_id=fixture.space_id,
        item_id=fixture.item_id,
        editor_session_id=acquire_command.editor_session_id,
        lease_id=acquire_result.lease_id,
        fencing_generation=acquire_result.fencing_generation,
        operation_id=_uuid(f"release:{label}"),
        request_id=_uuid(f"release-request:{label}"),
        raw_token=acquire_command.raw_token,
        request_meta={"method": "POST", "path": "/lease/release"},
    )


def _counts(factory: ConnectionFactory) -> dict[str, int]:
    connection = factory.open(read_only=True)
    try:
        return {
            table: int(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
            for table in ("operation_dedup", "audit_event", "outbox_event")
        }
    finally:
        connection.close()


def test_token_contract_and_command_repr_never_disclose_raw_secret() -> None:
    token = _token("repr")
    command = AcquireLeaseCommand(
        principal=SessionPrincipal("editor", 1),
        space_id=_uuid("space"),
        item_id=_uuid("item"),
        editor_session_id=_uuid("session"),
        operation_id=_uuid("operation"),
        request_id=_uuid("request"),
        raw_token=token,
    )
    assert token not in repr(command)
    key_ring = LeaseHmacKeyRing(
        active_key_id="lease-v1", keys={"lease-v1": _KEY_ONE}
    )
    assert _KEY_ONE.hex() not in repr(key_ring)
    expected = hashlib.sha256(b"unrelated").digest()
    observed = key_ring.digest(
        key_id="lease-v1",
        item_id=command.item_id,
        raw_token=base64.urlsafe_b64decode(token + "="),
    )
    assert observed != expected
    assert observed == hmac.digest(
        _KEY_ONE,
        b"knowledge-lease-v1\x00"
        + command.item_id.encode("utf-8")
        + b"\x00"
        + base64.urlsafe_b64decode(token + "="),
        "sha256",
    )


def test_token_parser_and_force_reason_fail_closed_before_mutation(
    isolated_knowledge_layout,
) -> None:
    fixture = _seed_part_fixture(isolated_knowledge_layout)
    service = _service(fixture)
    valid = AcquireLeaseCommand(
        principal=fixture.principal("editor"),
        space_id=fixture.space_id,
        item_id=fixture.item_id,
        editor_session_id=_uuid("strict-token-session"),
        operation_id=_uuid("strict-token-operation"),
        request_id=_uuid("strict-token-request"),
        raw_token=_token("strict-token"),
    )
    baseline = _counts(fixture.factory)
    for invalid in (
        "A" * 42,
        "A" * 44,
        "A" * 42 + "=",
        "A" * 42 + " ",
        "A" * 42 + "+",
        "\u00e9" * 43,
    ):
        with pytest.raises(LeaseValidationError) as error:
            service.acquire(replace(valid, raw_token=invalid))
        assert error.value.code == "VALIDATION_ERROR"
    with pytest.raises(LeaseValidationError) as error:
        service.force_release(
            ForceReleaseLeaseCommand(
                principal=fixture.principal("leader"),
                space_id=fixture.space_id,
                item_id=fixture.item_id,
                operation_id=_uuid("blank-force"),
                request_id=_uuid("blank-force-request"),
                reason="   ",
                confirmation="FORCE_RELEASE",
            )
        )
    assert error.value.code == "VALIDATION_ERROR"
    assert _counts(fixture.factory) == baseline


def test_acquire_heartbeat_release_and_exact_replays_are_durable(
    isolated_knowledge_layout,
) -> None:
    fixture = _seed_part_fixture(isolated_knowledge_layout)
    service = _service(fixture)
    baseline = _counts(fixture.factory)
    acquire_command, acquired = _acquire(fixture, service=service)
    assert acquired.fencing_generation == 1
    assert acquired.base_revision_id == fixture.revision_id
    assert acquired.lease_id not in acquire_command.raw_token

    replay = service.acquire(
        replace(acquire_command, request_id=_uuid("acquire-replay-request"))
    )
    assert replace(replay, request_id=acquired.request_id) == acquired
    assert _heartbeat(fixture, acquire_command, acquired, service=service).lease_id == acquired.lease_id

    release_command = _release_command(fixture, acquire_command, acquired)
    released = service.release(release_command)
    assert released.status == "released"
    replay_release = service.release(
        replace(release_command, request_id=_uuid("release-replay-request"))
    )
    assert replace(replay_release, request_id=released.request_id) == released

    connection = fixture.factory.open(read_only=True)
    try:
        lease = LeaseRepository().get(connection, fixture.item_id)
        assert lease is not None and not lease.held
        assert lease.fencing_generation == 1
        after = _counts(fixture.factory)
        assert {name: after[name] - baseline[name] for name in after} == {
            "operation_dedup": 2,
            "audit_event": 2,
            "outbox_event": 2,
        }
    finally:
        connection.close()


@pytest.mark.scenario("LEASE-002")
def test_foreign_same_part_member_receives_stable_lease_held(
    isolated_knowledge_layout,
) -> None:
    fixture = _seed_part_fixture(isolated_knowledge_layout)
    command, _ = _acquire(fixture)
    service = _service(fixture)
    foreign = replace(
        command,
        principal=fixture.principal("member"),
        editor_session_id=_uuid("member-session"),
        operation_id=_uuid("member-acquire"),
        request_id=_uuid("member-acquire-request"),
        raw_token=_token("member"),
    )
    with pytest.raises(LeaseHeldError) as first:
        service.acquire(foreign)
    assert first.value.code == "LEASE_HELD"
    assert first.value.details["holder_user_id"] == "editor"
    with pytest.raises(LeaseHeldError) as replay:
        service.acquire(replace(foreign, request_id=_uuid("member-retry")))
    assert replay.value.as_error() == first.value.as_error()


@pytest.mark.scenario("LEASE-004")
def test_expiry_reclaim_and_force_release_keep_generation_strictly_increasing(
    isolated_knowledge_layout,
) -> None:
    fixture = _seed_part_fixture(isolated_knowledge_layout)
    short = _service(fixture, stale_after_ms=1)
    first_command, first = _acquire(fixture, service=short)
    time.sleep(0.01)
    with pytest.raises(LeaseLostError):
        _heartbeat(fixture, first_command, first, service=short)

    second_command, second = _acquire(
        fixture, user_id="member", label="reclaim", service=short
    )
    assert second.fencing_generation == first.fencing_generation + 1
    forced = short.force_release(
        ForceReleaseLeaseCommand(
            principal=fixture.principal("leader"),
            space_id=fixture.space_id,
            item_id=fixture.item_id,
            operation_id=_uuid("force-release"),
            request_id=_uuid("force-release-request"),
            reason="Editor workstation is unavailable",
            confirmation="FORCE_RELEASE",
        )
    )
    assert forced.status == "force_released"
    third_command, third = _acquire(
        fixture, user_id="editor", label="after-force", service=short
    )
    assert third.fencing_generation == second.fencing_generation + 2
    assert third_command.raw_token != second_command.raw_token


@pytest.mark.scenario("LEASE-007")
@pytest.mark.scenario("LEASE-008")
def test_only_same_part_leader_or_admin_can_force_release_with_reason(
    isolated_knowledge_layout,
) -> None:
    fixture = _seed_part_fixture(isolated_knowledge_layout)
    acquire_command, acquired = _acquire(fixture)
    service = _service(fixture)
    with pytest.raises(LeaseAuthorizationError):
        service.force_release(
            ForceReleaseLeaseCommand(
                principal=fixture.principal("member"),
                space_id=fixture.space_id,
                item_id=fixture.item_id,
                operation_id=_uuid("ordinary-force"),
                request_id=_uuid("ordinary-force-request"),
                reason="Not authorized",
                confirmation="FORCE_RELEASE",
            )
        )
    result = service.force_release(
        ForceReleaseLeaseCommand(
            principal=fixture.principal("leader"),
            space_id=fixture.space_id,
            item_id=fixture.item_id,
            operation_id=_uuid("leader-force"),
            request_id=_uuid("leader-force-request"),
            reason="Editor confirmed abandoned draft",
            confirmation="FORCE_RELEASE",
        )
    )
    assert result.status == "force_released"
    with pytest.raises(LeaseLostError):
        _heartbeat(fixture, acquire_command, acquired, service=service)
    _, reacquired = _acquire(
        fixture, user_id="editor", label="admin-force", service=service
    )
    admin_result = service.force_release(
        ForceReleaseLeaseCommand(
            principal=fixture.principal("admin"),
            space_id=fixture.space_id,
            item_id=fixture.item_id,
            operation_id=_uuid("admin-force"),
            request_id=_uuid("admin-force-request"),
            reason="System administrator confirmed abandoned editor",
            confirmation="FORCE_RELEASE",
        )
    )
    assert admin_result.status == "force_released"
    assert reacquired.fencing_generation == 3
    connection = fixture.factory.open(read_only=True)
    try:
        audits = connection.execute(
            """SELECT reason,details_json FROM audit_event
                WHERE action='knowledge_lease.force_released' ORDER BY id"""
        ).fetchall()
        assert [row[0] for row in audits] == [
            "Editor confirmed abandoned draft",
            "System administrator confirmed abandoned editor",
        ]
        details = json.loads(audits[0][1])
        assert details["previous_holder_user_id"] == "editor"
        assert details["new_fencing_generation"] == 2
        assert json.loads(audits[1][1])["new_fencing_generation"] == 4
    finally:
        connection.close()


def test_key_rotation_verifies_existing_lease_and_terminal_replay(
    isolated_knowledge_layout,
) -> None:
    fixture = _seed_part_fixture(isolated_knowledge_layout)
    old = _service(fixture)
    acquire_command, acquired = _acquire(fixture, service=old)
    rotated = _service(
        fixture,
        keys=LeaseHmacKeyRing(
            active_key_id="lease-v2",
            keys={"lease-v1": _KEY_ONE, "lease-v2": _KEY_TWO},
        ),
    )
    heartbeat = _heartbeat(fixture, acquire_command, acquired, service=rotated)
    assert heartbeat.fencing_generation == acquired.fencing_generation
    release_command = _release_command(fixture, acquire_command, acquired, label="rotate")
    released = rotated.release(release_command)
    replay = rotated.release(
        replace(release_command, request_id=_uuid("rotated-release-replay"))
    )
    assert replace(replay, request_id=released.request_id) == released


def test_validate_edit_fence_reports_draft_recovery_without_overwrite(
    isolated_knowledge_layout,
) -> None:
    fixture = _seed_part_fixture(isolated_knowledge_layout)
    service = _service(fixture)
    command, acquired = _acquire(fixture, service=service)
    check = EditFenceCheck(
        principal=command.principal,
        space_id=fixture.space_id,
        item_id=fixture.item_id,
        editor_session_id=command.editor_session_id,
        lease_id=acquired.lease_id,
        fencing_generation=acquired.fencing_generation,
        expected_item_row_version=999,
        expected_revision_id=fixture.revision_id,
        raw_token=command.raw_token,
    )
    connection = fixture.factory.open()
    try:
        with transaction(connection):
            with pytest.raises(LeaseRevisionConflictError) as error:
                service.validate_edit_fence_in_transaction(connection, check)
            recovery = error.value.details["draft_recovery"]
            assert recovery["preserve_local_draft"] is True
            assert recovery["force_overwrite_allowed"] is False
            assert recovery["actions"] == [
                "view_current",
                "copy_draft",
                "reacquire_and_compare",
            ]
            with pytest.raises(LeaseLostError) as lost:
                service.validate_edit_fence_in_transaction(
                    connection,
                    replace(check, expected_item_row_version=1, raw_token=_token("wrong")),
                )
            assert lost.value.details["draft_recovery"]["preserve_local_draft"] is True
    finally:
        connection.close()


def test_membership_move_revokes_fence_and_rejects_later_heartbeat(
    isolated_knowledge_layout,
) -> None:
    fixture = _seed_part_fixture(isolated_knowledge_layout)
    service = _service(fixture)
    command, acquired = _acquire(fixture, service=service)
    organization = OrganizationRepository()
    users = UserRepository()
    connection = fixture.factory.open()
    try:
        with transaction(connection):
            membership = organization.get_current_membership(connection, "editor")
            assert membership is not None
            actor = users.require(connection, "editor")
            organization.close_current_membership(
                connection,
                user_id="editor",
                expected_membership_id=membership.id,
                closed_at_ms=10_000,
            )
            organization.compare_and_swap_user_part(
                connection,
                user_id="editor",
                expected_current_part_id=fixture.part_id,
                target_part_id=None,
                expected_scope_epoch=actor.scope_epoch,
                expected_row_version=actor.row_version,
                updated_at_ms=10_000,
            )
            revoked = organization.revoke_user_part_leases(
                connection, user_id="editor", part_id=fixture.part_id
            )
            assert revoked.revoked_lease_count == 1
    finally:
        connection.close()
    with pytest.raises(LeaseScopeStaleError):
        _heartbeat(fixture, command, acquired, service=service)


@pytest.mark.scenario("LEASE-011")
def test_raw_token_never_appears_in_database_audit_outbox_or_result(
    isolated_knowledge_layout,
    caplog,
) -> None:
    fixture = _seed_part_fixture(isolated_knowledge_layout)
    service = _service(fixture)
    command, acquired = _acquire(fixture, label="secret-sentinel", service=service)
    token = command.raw_token
    raw = base64.urlsafe_b64decode(token + "=")
    assert token not in repr(command)
    assert token not in json.dumps(acquired.as_dict(), sort_keys=True)
    assert token not in caplog.text

    connection = fixture.factory.open(read_only=True)
    try:
        rows = []
        for table in ("operation_dedup", "audit_event", "outbox_event"):
            rows.extend(tuple(row) for row in connection.execute(f"SELECT * FROM {table}"))
        encoded = repr(rows).encode("utf-8")
        assert token.encode("ascii") not in encoded
        assert raw not in encoded
        lease = LeaseRepository().get(connection, fixture.item_id)
        assert lease is not None and lease.token_hmac is not None
        assert lease.token_hmac != raw
    finally:
        connection.close()
    for path in Path(isolated_knowledge_layout.root).rglob("*"):
        if path.is_file():
            data = path.read_bytes()
            assert token.encode("ascii") not in data
            assert raw not in data


@pytest.mark.parametrize(
    "failpoint",
    [
        "lease.after_generation_increment",
        "lease.after_holder_write",
        "lease.before_commit",
    ],
)
def test_acquire_failpoints_roll_back_fence_holder_dedup_audit_and_outbox(
    isolated_knowledge_layout,
    failpoint: str,
) -> None:
    fixture = _seed_part_fixture(isolated_knowledge_layout)
    baseline = _counts(fixture.factory)

    def inject(name: str) -> None:
        if name == failpoint:
            raise RuntimeError(failpoint)

    with pytest.raises(RuntimeError, match=failpoint):
        _acquire(fixture, service=_service(fixture, failpoint=inject))
    connection = fixture.factory.open(read_only=True)
    try:
        lease = LeaseRepository().get(connection, fixture.item_id)
        assert lease is not None and not lease.held
        assert lease.fencing_generation == 0
        assert _counts(fixture.factory) == baseline
    finally:
        connection.close()


def test_same_operation_with_different_token_is_idempotency_conflict(
    isolated_knowledge_layout,
) -> None:
    fixture = _seed_part_fixture(isolated_knowledge_layout)
    service = _service(fixture)
    command, _ = _acquire(fixture, service=service)
    with pytest.raises(LeaseIdempotencyError):
        service.acquire(replace(command, raw_token=_token("different")))


@pytest.mark.parametrize(
    "failpoint",
    ["lease.force_release_after_generation", "lease.before_commit"],
)
def test_force_release_failpoints_leave_original_holder_and_fence_intact(
    isolated_knowledge_layout,
    failpoint: str,
) -> None:
    fixture = _seed_part_fixture(isolated_knowledge_layout)
    _acquire(fixture)
    baseline = _counts(fixture.factory)

    def inject(name: str) -> None:
        if name == failpoint:
            raise RuntimeError(failpoint)

    with pytest.raises(RuntimeError, match=failpoint):
        _service(fixture, failpoint=inject).force_release(
            ForceReleaseLeaseCommand(
                principal=fixture.principal("leader"),
                space_id=fixture.space_id,
                item_id=fixture.item_id,
                operation_id=_uuid(f"force-fail:{failpoint}"),
                request_id=_uuid(f"force-fail-request:{failpoint}"),
                reason="Injected rollback proof",
                confirmation="FORCE_RELEASE",
            )
        )
    connection = fixture.factory.open(read_only=True)
    try:
        lease = LeaseRepository().get(connection, fixture.item_id)
        assert lease is not None and lease.held
        assert lease.holder_user_id == "editor"
        assert lease.fencing_generation == 1
        assert _counts(fixture.factory) == baseline
    finally:
        connection.close()


_LEASE_PROCESS_WORKER = r"""
import json
from pathlib import Path
import sqlite3
import sys
import time

from code_assist_v1.knowledge_core.db import ConnectionFactory
from code_assist_v1.knowledge_core.domain import FeatureFlags
from code_assist_v1.knowledge_core.migrations import CANONICAL_MIGRATION_CATALOG
from code_assist_v1.knowledge_core.scope import ScopeResolver, SessionPrincipal
from code_assist_v1.knowledge_core.services.editing import (
    AcquireLeaseCommand,
    FencedLeaseService,
    LeaseHmacKeyRing,
    LeaseServiceError,
)

database, ready_raw, start_raw, payload_raw = sys.argv[1:]
payload = json.loads(payload_raw)
factory = ConnectionFactory._for_isolated_tests(
    Path(database), CANONICAL_MIGRATION_CATALOG
)
service = FencedLeaseService(
    factory,
    key_ring=LeaseHmacKeyRing(
        active_key_id="lease-v1",
        keys={"lease-v1": bytes.fromhex(payload.pop("key_hex"))},
    ),
    scope_resolver=ScopeResolver(FeatureFlags(
        core_enabled=True,
        personal_reads=True,
        personal_writes=True,
        part_reads=True,
        part_writes=True,
    )),
)
payload["principal"] = SessionPrincipal(**payload["principal"])
ready = Path(ready_raw)
start = Path(start_raw)
ready.touch()
deadline = time.monotonic() + 60.0
while not start.exists():
    if time.monotonic() >= deadline:
        raise SystemExit("lease barrier timed out")
    time.sleep(0.01)
try:
    result = service.acquire(AcquireLeaseCommand(**payload))
    print(json.dumps({"kind": "ok", "result": result.as_dict()}, sort_keys=True))
except LeaseServiceError as exc:
    print(json.dumps({"code": exc.code, "kind": "service_error"}, sort_keys=True))
except sqlite3.OperationalError as exc:
    message = str(exc)
    kind = "sqlite_busy" if "busy" in message.lower() or "locked" in message.lower() else "sqlite_error"
    print(json.dumps({"kind": kind}, sort_keys=True))
"""


@pytest.mark.scenario("LEASE-009")
def test_forty_real_process_acquirers_produce_exactly_one_valid_holder(
    isolated_knowledge_layout,
) -> None:
    fixture = _seed_part_fixture(isolated_knowledge_layout)
    start = isolated_knowledge_layout.root / "lease-process-start"
    ready_paths = tuple(
        isolated_knowledge_layout.root / f"lease-process-ready-{index}"
        for index in range(40)
    )
    environment = os.environ.copy()
    processes: list[subprocess.Popen[str]] = []
    for index, ready in enumerate(ready_paths):
        payload = {
            "editor_session_id": _uuid(f"process-session:{index}"),
            "item_id": fixture.item_id,
            "key_hex": _KEY_ONE.hex(),
            "operation_id": _uuid(f"process-operation:{index}"),
            "principal": {
                "captured_scope_epoch": fixture.epochs["editor"],
                "user_id": "editor",
            },
            "raw_token": _token(f"process-token:{index}"),
            "request_id": _uuid(f"process-request:{index}"),
            "request_meta": {},
            "space_id": fixture.space_id,
        }
        processes.append(
            subprocess.Popen(
                [
                    sys.executable,
                    "-E",
                    "-c",
                    _LEASE_PROCESS_WORKER,
                    str(isolated_knowledge_layout.core_db_path),
                    str(ready),
                    str(start),
                    json.dumps(payload, sort_keys=True),
                ],
                cwd=_PACKAGE_ROOT.parent,
                env=environment,
                text=True,
                encoding="utf-8",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        )
    deadline = time.monotonic() + 60.0
    while not all(path.exists() for path in ready_paths):
        for process in processes:
            if process.poll() is not None:
                stdout, stderr = process.communicate()
                raise AssertionError(
                    f"lease worker exited before barrier: {process.returncode}; "
                    f"stdout={stdout!r}; stderr={stderr!r}"
                )
        if time.monotonic() >= deadline:
            raise AssertionError("lease workers did not reach the start barrier")
        time.sleep(0.01)
    start.touch()

    results: list[dict[str, object]] = []
    for process in processes:
        stdout, stderr = process.communicate(timeout=90)
        assert process.returncode == 0, (stdout, stderr)
        assert stderr == ""
        lines = [line for line in stdout.splitlines() if line.strip()]
        assert lines
        results.append(json.loads(lines[-1]))
    assert not any(value["kind"] == "sqlite_busy" for value in results), results
    winners = [value for value in results if value["kind"] == "ok"]
    held = [
        value
        for value in results
        if value == {"code": "LEASE_HELD", "kind": "service_error"}
    ]
    assert len(winners) == 1
    assert len(held) == 39

    connection = fixture.factory.open(read_only=True)
    try:
        lease = LeaseRepository().get(connection, fixture.item_id)
        assert lease is not None and lease.held
        assert lease.holder_user_id == "editor"
        assert lease.fencing_generation == 1
    finally:
        connection.close()

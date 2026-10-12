from __future__ import annotations

import json
import os
import subprocess
import sys
from uuid import UUID, uuid5

import pytest

from code_assist_v1.knowledge_core.db import (
    ConnectionFactory,
    TransactionMode,
    transaction,
)
from code_assist_v1.knowledge_core.domain import FeatureFlags
from code_assist_v1.knowledge_core.errors import (
    KnowledgeCoreError,
    KnowledgeCoreTransactionError,
)
from code_assist_v1.knowledge_core.migrations import (
    CANONICAL_MIGRATION_CATALOG,
    MigrationStorageLayout,
    SingleMigrator,
)
from code_assist_v1.knowledge_core.repositories.organization import (
    OrganizationRepository,
    RESPONSIBILITY_ROOT_ID,
)
from code_assist_v1.knowledge_core.repositories.affiliations import (
    OrgAffiliationRepository,
)
from code_assist_v1.knowledge_core.repositories.higher_spaces import (
    HigherSpaceRepository,
)
from code_assist_v1.knowledge_core.repositories.spaces import SpaceRepository
from code_assist_v1.knowledge_core.repositories.users import (
    LegacyUserRecord,
    UserRepository,
)
from code_assist_v1.knowledge_core.scope import (
    Capability,
    ScopeAuthenticationRequiredError,
    ScopeForbiddenError,
    ScopeIntegrityError,
    ScopeNotFoundError,
    ScopeResolver,
    ScopeStaleError,
    ScopeValidationError,
    SessionPrincipal,
)


pytestmark = pytest.mark.unit
_NAMESPACE = UUID("99ed0c84-55ae-402b-97d7-6ef6aeb84b77")
_ORDINARY_CAPABILITIES = (
    Capability.READ,
    Capability.PUBLISH,
    Capability.EDIT,
    Capability.DEACTIVATE,
    Capability.RESTORE,
    Capability.CURATE,
    Capability.TEMPORAL_MUTATE,
    Capability.LOCAL_EDGE_CORRECT,
)
_PART_LEADER_CAPABILITIES = (*_ORDINARY_CAPABILITIES, Capability.FORCE_RELEASE)
_HIGHER_NATIVE_CAPABILITIES = (
    *_ORDINARY_CAPABILITIES,
    Capability.AGGREGATE_EDGE_CORRECT,
    Capability.DISTRIBUTION_GRANT,
    Capability.DISTRIBUTION_REVOKE,
    Capability.FORCE_RELEASE,
)


def _uuid(label: str) -> str:
    return str(uuid5(_NAMESPACE, label))


def _factory(layout) -> ConnectionFactory:
    factory = ConnectionFactory._for_isolated_tests(
        layout.core_db_path, CANONICAL_MIGRATION_CATALOG
    )
    SingleMigrator(
        factory,
        app_version="p1.7-scope-resolver-test",
        storage_layout=MigrationStorageLayout(
            data_root=layout.root,
            database=layout.core_db_path,
            blob_root=layout.blobs_dir,
            projection_root=layout.projections_dir,
        ),
    ).run()
    return factory


def _user(user_id: str, *, role: str = "user", disabled: bool = False):
    return LegacyUserRecord(
        id=user_id,
        display_name=user_id.title(),
        password_hash=f"scrypt:p1.7:{user_id}",
        system_role=role,  # type: ignore[arg-type]
        account_status="disabled" if disabled else "active",
        created_at_ms=100,
        updated_at_ms=100,
        disabled_at_ms=100 if disabled else None,
    )


def _enabled_flags(**overrides: bool) -> FeatureFlags:
    values = {
        "core_enabled": True,
        "personal_reads": True,
        "personal_writes": True,
        "part_reads": True,
        "part_writes": True,
        "projections": False,
        "higher_spaces": False,
    }
    values.update(overrides)
    return FeatureFlags(**values)


def _seed(factory: ConnectionFactory) -> dict[str, str]:
    users = UserRepository()
    organization = OrganizationRepository()
    spaces = SpaceRepository()
    ids = {
        "team_a": _uuid("team:a"),
        "team_b": _uuid("team:b"),
        "part_a": _uuid("part:a"),
        "part_b": _uuid("part:b"),
        "personal_member": _uuid("space:personal:member"),
        "personal_colleague": _uuid("space:personal:colleague"),
        "personal_other": _uuid("space:personal:other"),
        "personal_admin": _uuid("space:personal:admin"),
        "personal_unassigned": _uuid("space:personal:unassigned"),
        "personal_disabled": _uuid("space:personal:disabled"),
        "personal_team_leader": _uuid("space:personal:team-leader"),
        "personal_responsibility_executive": _uuid(
            "space:personal:responsibility-executive"
        ),
        "part_space_a": _uuid("space:part:a"),
        "part_space_b": _uuid("space:part:b"),
    }
    ids["team_space_a"] = ids["team_a"]
    ids["team_space_b"] = ids["team_b"]
    ids["responsibility_space"] = RESPONSIBILITY_ROOT_ID
    connection = factory.open()
    try:
        with transaction(connection):
            users.import_legacy_many(
                connection,
                (
                    _user("admin", role="admin"),
                    _user("member"),
                    _user("colleague"),
                    _user("other"),
                    _user("unassigned"),
                    _user("team_leader"),
                    _user("responsibility_executive"),
                    _user("disabled", disabled=True),
                ),
            )
            organization.create_unit(
                connection,
                unit_id=ids["team_a"],
                kind="team",
                parent_id=RESPONSIBILITY_ROOT_ID,
                name="Team A",
                actor_user_id="admin",
                now_ms=1000,
            )
            organization.create_unit(
                connection,
                unit_id=ids["team_b"],
                kind="team",
                parent_id=RESPONSIBILITY_ROOT_ID,
                name="Team B",
                actor_user_id="admin",
                now_ms=1000,
            )
            for key, parent, name in (
                ("part_a", "team_a", "Part A"),
                ("part_b", "team_b", "Part B"),
            ):
                organization.create_unit(
                    connection,
                    unit_id=ids[key],
                    kind="part",
                    parent_id=ids[parent],
                    name=name,
                    actor_user_id="admin",
                    now_ms=1001,
                )
            for user_id, part_key in (
                ("member", "part_a"),
                ("colleague", "part_a"),
                ("admin", "part_a"),
                ("other", "part_b"),
            ):
                state = organization.get_user_organization_state(connection, user_id)
                assert state is not None
                membership = organization.open_membership(
                    connection,
                    membership_id=_uuid(f"membership:{user_id}"),
                    user_id=user_id,
                    part_id=ids[part_key],
                    assigned_by="admin",
                    reason="Scope fixture assignment",
                    valid_from_ms=1100,
                )
                assert membership.part_id == ids[part_key]
                organization.compare_and_swap_user_part(
                    connection,
                    user_id=user_id,
                    expected_current_part_id=None,
                    target_part_id=ids[part_key],
                    expected_scope_epoch=state.scope_epoch,
                    expected_row_version=state.row_version,
                    updated_at_ms=1100,
                )
            organization.open_part_leader(
                connection,
                assignment_id=_uuid("leader:part:a"),
                user_id="member",
                part_id=ids["part_a"],
                assigned_by="admin",
                reason="Scope fixture leader",
                valid_from_ms=1200,
            )
            organization.open_part_leader(
                connection,
                assignment_id=_uuid("leader:part:b"),
                user_id="other",
                part_id=ids["part_b"],
                assigned_by="admin",
                reason="Scope fixture leader",
                valid_from_ms=1200,
            )
            for user_id in (
                "member",
                "colleague",
                "other",
                "admin",
                "unassigned",
                "team_leader",
                "responsibility_executive",
                "disabled",
            ):
                spaces.ensure_personal(
                    connection,
                    space_id=ids[f"personal_{user_id}"],
                    user_id=user_id,
                    created_at_ms=1300,
                )
            for key, part_key in (
                ("part_space_a", "part_a"),
                ("part_space_b", "part_b"),
            ):
                spaces.ensure_part(
                    connection,
                    space_id=ids[key],
                    part_id=ids[part_key],
                    created_at_ms=1300,
                )
            for key, team_key in (
                ("team_space_a", "team_a"),
                ("team_space_b", "team_b"),
            ):
                connection.execute(
                    """INSERT INTO knowledge_space(
                         id,space_kind,owner_user_id,owner_org_unit_id,status,
                         write_enabled,generation,created_at,updated_at,row_version
                       ) VALUES (?,'team',NULL,?,'active',0,0,1300,1300,1)""",
                    (ids[key], ids[team_key]),
                )
        return ids
    finally:
        connection.close()


def _principal(connection, user_id: str) -> SessionPrincipal:
    user = UserRepository().require(connection, user_id)
    return SessionPrincipal(user_id=user.id, captured_scope_epoch=user.scope_epoch)


def _set_write(connection, space_id: str, *, enabled: bool, now_ms: int = 2000):
    repository = SpaceRepository()
    current = repository.require_by_id(connection, space_id)
    return repository.update_state(
        connection,
        space_id=space_id,
        expected_row_version=current.row_version,
        status=current.status,
        write_enabled=enabled,
        updated_at_ms=now_ms,
    )


@pytest.mark.scenario("SPACE-001")
@pytest.mark.scenario("ORG-004")
def test_personal_owner_and_unassigned_user_receive_only_own_scope(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    ids = _seed(factory)
    connection = factory.open()
    try:
        with transaction(connection):
            _set_write(connection, ids["personal_unassigned"], enabled=True)
        with transaction(connection, mode=TransactionMode.DEFERRED):
            principal = _principal(connection, "unassigned")
            result = ScopeResolver(_enabled_flags()).list_selectable_in_transaction(
                connection, principal
            )
            assert [(item.kind, item.id) for item in result.items] == [
                ("personal", ids["personal_unassigned"])
            ]
            assert result.items[0].name == "내 지식"
            assert result.items[0].org_path == ()
            assert result.items[0].capabilities == _ORDINARY_CAPABILITIES
            resolved = ScopeResolver(_enabled_flags()).resolve_in_transaction(
                connection,
                principal,
                ids["personal_unassigned"],
                Capability.EDIT,
            )
            assert resolved.entitled_capabilities == _ORDINARY_CAPABILITIES
            assert resolved.effective_capabilities == _ORDINARY_CAPABILITIES
            assert resolved.is_part_leader is False
            with pytest.raises(ScopeNotFoundError):
                ScopeResolver(_enabled_flags()).resolve_in_transaction(
                    connection, principal, ids["personal_member"], Capability.READ
                )
    finally:
        connection.close()


@pytest.mark.scenario("SPACE-002")
@pytest.mark.scenario("ORG-006")
@pytest.mark.scenario("TIME-015")
def test_current_part_member_and_leader_capabilities_are_db_and_rollout_derived(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    ids = _seed(factory)
    connection = factory.open()
    try:
        with transaction(connection):
            _set_write(connection, ids["part_space_a"], enabled=True)
        with transaction(connection, mode=TransactionMode.DEFERRED):
            principal = _principal(connection, "member")
            scope = ScopeResolver(_enabled_flags()).resolve_in_transaction(
                connection, principal, ids["part_space_a"], "force_release"
            )
            assert scope.space.kind == "part"
            assert scope.space.name == "Part A"
            assert scope.space.org_path[-2:] == ("Team A", "Part A")
            assert len(scope.space.org_path) == 3
            assert scope.is_part_leader is True
            assert scope.entitled_capabilities == _PART_LEADER_CAPABILITIES
            assert scope.effective_capabilities == _PART_LEADER_CAPABILITIES
    finally:
        connection.close()


@pytest.mark.scenario("SPACE-002")
@pytest.mark.scenario("TIME-015")
def test_ordinary_nonleader_member_has_all_ordinary_but_no_force_release(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    ids = _seed(factory)
    connection = factory.open()
    try:
        with transaction(connection):
            _set_write(connection, ids["part_space_a"], enabled=True)
        with transaction(connection, mode=TransactionMode.DEFERRED):
            colleague = _principal(connection, "colleague")
            scope = ScopeResolver(_enabled_flags()).resolve_in_transaction(
                connection, colleague, ids["part_space_a"], Capability.CURATE
            )
            assert scope.is_part_leader is False
            ordinary = _ORDINARY_CAPABILITIES
            assert scope.entitled_capabilities == ordinary
            assert scope.effective_capabilities == ordinary
            with pytest.raises(ScopeForbiddenError):
                ScopeResolver(_enabled_flags()).resolve_in_transaction(
                    connection,
                    colleague,
                    ids["part_space_a"],
                    Capability.FORCE_RELEASE,
                )
    finally:
        connection.close()


@pytest.mark.scenario("SPACE-003")
@pytest.mark.scenario("SPACE-004")
def test_same_part_admin_can_force_release_but_cannot_widen_agent_scope(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    ids = _seed(factory)
    connection = factory.open()
    try:
        with transaction(connection):
            _set_write(connection, ids["part_space_a"], enabled=True)
        with transaction(connection, mode=TransactionMode.DEFERRED):
            admin = _principal(connection, "admin")
            scope = ScopeResolver(_enabled_flags()).resolve_in_transaction(
                connection, admin, ids["part_space_a"], Capability.FORCE_RELEASE
            )
            assert scope.system_role == "admin"
            assert scope.is_part_leader is False
            assert scope.effective_capabilities == _PART_LEADER_CAPABILITIES
            for foreign in (ids["part_space_b"], ids["personal_other"]):
                with pytest.raises(ScopeNotFoundError):
                    ScopeResolver(_enabled_flags()).resolve_in_transaction(
                        connection, admin, foreign, Capability.READ
                    )
    finally:
        connection.close()


@pytest.mark.scenario("SPACE-003")
@pytest.mark.scenario("SPACE-004")
def test_foreign_former_and_forged_spaces_are_intentionally_undisclosed(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    ids = _seed(factory)
    connection = factory.open()
    try:
        with transaction(connection, mode=TransactionMode.DEFERRED):
            principal = _principal(connection, "member")
            resolver = ScopeResolver(_enabled_flags())
            for space_id in (
                ids["part_space_b"],
                ids["personal_other"],
                _uuid("missing-space"),
            ):
                with pytest.raises(ScopeNotFoundError) as caught:
                    resolver.resolve_in_transaction(
                        connection, principal, space_id, Capability.READ
                    )
                assert caught.value.code == "NOT_FOUND"
                assert caught.value.http_status == 404
            with pytest.raises(ScopeValidationError):
                resolver.resolve_in_transaction(
                    connection, principal, "not-a-uuid", Capability.READ
                )
            with pytest.raises(ScopeValidationError):
                resolver.resolve_in_transaction(
                    connection, principal, ids["part_space_a"], None  # type: ignore[arg-type]
                )
    finally:
        connection.close()


@pytest.mark.scenario("HSPACE-007")
@pytest.mark.scenario("HSPACE-009")
def test_higher_read_flag_exposes_exact_ancestor_view_without_admin_authorship(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    ids = _seed(factory)
    higher_space = ids["team_space_a"]
    connection = factory.open()
    try:
        resolver = ScopeResolver(_enabled_flags(higher_spaces=True))
        with transaction(connection, mode=TransactionMode.DEFERRED):
            principal = _principal(connection, "admin")
            plan = resolver.resolve_access_plan_in_transaction(
                connection, principal, higher_space, Capability.READ
            )
            assert plan.selected_view.kind == "team"
            assert plan.native_mutation_space_id is None
            assert [source.source_space_id for source in plan.corpus_closure.sources] == [
                higher_space,
                ids["part_space_a"],
            ]
            assert all(
                source.capabilities == (Capability.READ,)
                for source in plan.corpus_closure.sources
            )
            listed = resolver.list_selectable_in_transaction(connection, principal)
            assert [item.kind for item in listed.items] == [
                "personal",
                "part",
                "team",
                "responsibility",
            ]
            assert higher_space in {item.id for item in listed.items}
    finally:
        connection.close()


@pytest.mark.scenario("HSPACE-006")
@pytest.mark.scenario("HSPACE-007")
@pytest.mark.scenario("HSPACE-008")
@pytest.mark.scenario("HSPACE-009")
@pytest.mark.scenario("HSPACE-010")
@pytest.mark.scenario("HSPACE-012")
@pytest.mark.scenario("TIME-015")
def test_higher_closure_and_native_capabilities_follow_exact_current_episode(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    ids = _seed(factory)
    higher = HigherSpaceRepository()
    spaces = SpaceRepository()
    team_role_id = _uuid("higher-role:team-a:direct-leader")
    responsibility_role_id = _uuid("higher-role:responsibility:direct-executive")
    affiliations = OrgAffiliationRepository()
    connection = factory.open()
    try:
        with transaction(connection):
            affiliations.open_direct(
                connection,
                affiliation_id=team_role_id,
                user_id="team_leader",
                org_unit_id=ids["team_a"],
                home_kind="team",
                assigned_by_user_id="admin",
                reason="Direct Team fixture affiliation",
                valid_from_ms=2998,
            )
            affiliations.bump_direct_scope(
                connection,
                user_id="team_leader",
                expected_scope_epoch=1,
                expected_row_version=1,
                updated_at_ms=2999,
            )
            higher.insert_open_role(
                connection,
                assignment_id=team_role_id,
                org_unit_id=ids["team_a"],
                user_id="team_leader",
                role="team_leader",
                assigned_by_user_id="admin",
                assignment_generation=1,
                assigned_at_ms=3000,
            )
            affiliations.open_direct(
                connection,
                affiliation_id=responsibility_role_id,
                user_id="responsibility_executive",
                org_unit_id=RESPONSIBILITY_ROOT_ID,
                home_kind="responsibility",
                assigned_by_user_id="admin",
                reason="Direct Responsibility fixture affiliation",
                valid_from_ms=2999,
            )
            affiliations.bump_direct_scope(
                connection,
                user_id="responsibility_executive",
                expected_scope_epoch=1,
                expected_row_version=1,
                updated_at_ms=3000,
            )
            higher.insert_open_role(
                connection,
                assignment_id=responsibility_role_id,
                org_unit_id=RESPONSIBILITY_ROOT_ID,
                user_id="responsibility_executive",
                role="responsibility_executive",
                assigned_by_user_id="admin",
                assignment_generation=1,
                assigned_at_ms=3001,
            )
            for key, now_ms in (
                ("team_space_a", 3010),
                ("responsibility_space", 3011),
            ):
                current = spaces.require_by_id(connection, ids[key])
                spaces.update_state(
                    connection,
                    space_id=current.id,
                    expected_row_version=current.row_version,
                    status="active",
                    write_enabled=True,
                    updated_at_ms=now_ms,
                )

        flags = _enabled_flags(
            higher_spaces=True,
            team_writes=True,
            responsibility_writes=True,
        )
        resolver = ScopeResolver(flags)
        with transaction(connection, mode=TransactionMode.DEFERRED):
            team_leader = _principal(connection, "team_leader")
            team = resolver.resolve_access_plan_in_transaction(
                connection, team_leader, ids["team_space_a"], Capability.EDIT
            )
            assert team.role_assignment_id == team_role_id
            assert team.role_assignment_generation == 1
            assert team.native_mutation_space_id == ids["team_space_a"]
            assert team.selected_view.capabilities == _HIGHER_NATIVE_CAPABILITIES
            assert [source.source_space_id for source in team.corpus_closure.sources] == [
                ids["team_space_a"],
                ids["part_space_a"],
            ]
            assert team.corpus_closure.sources[1].inherited is True
            assert team.corpus_closure.sources[1].capabilities == (Capability.READ,)

            responsibility = resolver.resolve_access_plan_in_transaction(
                connection,
                _principal(connection, "responsibility_executive"),
                ids["responsibility_space"],
                Capability.DISTRIBUTION_GRANT,
            )
            assert responsibility.role_assignment_id == responsibility_role_id
            assert responsibility.native_mutation_space_id == ids["responsibility_space"]
            assert {source.source_space_id for source in responsibility.corpus_closure.sources} == {
                ids["responsibility_space"],
                ids["team_space_a"],
                ids["team_space_b"],
                ids["part_space_a"],
                ids["part_space_b"],
            }
            assert responsibility.corpus_closure.sha256 != team.corpus_closure.sha256

            for ordinary_user in ("colleague", "admin"):
                ordinary = _principal(connection, ordinary_user)
                read_only = resolver.resolve_access_plan_in_transaction(
                    connection, ordinary, ids["team_space_a"], Capability.READ
                )
                assert read_only.selected_view.capabilities == (Capability.READ,)
                assert read_only.native_mutation_space_id is None
                with pytest.raises(ScopeForbiddenError):
                    resolver.resolve_access_plan_in_transaction(
                        connection, ordinary, ids["team_space_a"], Capability.EDIT
                    )

            foreign = _principal(connection, "other")
            with pytest.raises(ScopeNotFoundError):
                resolver.resolve_access_plan_in_transaction(
                    connection, foreign, ids["team_space_a"], Capability.READ
                )
    finally:
        connection.close()


@pytest.mark.scenario("SPACE-001")
@pytest.mark.scenario("SPACE-003")
@pytest.mark.parametrize(
    ("space_key", "user_id", "kind", "status"),
    (
        ("personal_member", "member", "personal", "frozen"),
        ("personal_member", "member", "personal", "inactive"),
        ("part_space_a", "member", "part", "frozen"),
        ("part_space_a", "member", "part", "inactive"),
    ),
)
def test_frozen_and_inactive_owned_spaces_are_hidden_and_unwritable(
    isolated_knowledge_layout,
    space_key: str,
    user_id: str,
    kind: str,
    status: str,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    ids = _seed(factory)
    connection = factory.open()
    try:
        repository = SpaceRepository()
        with transaction(connection):
            current = repository.require_by_id(connection, ids[space_key])
            repository.update_state(
                connection,
                space_id=current.id,
                expected_row_version=current.row_version,
                status=status,  # type: ignore[arg-type]
                write_enabled=False,
                updated_at_ms=3500,
            )
        with transaction(connection, mode=TransactionMode.DEFERRED):
            principal = _principal(connection, user_id)
            before = connection.execute(
                """SELECT
                     (SELECT count(*) FROM knowledge_space),
                     (SELECT count(*) FROM audit_event),
                     (SELECT count(*) FROM outbox_event)"""
            ).fetchone()
            resolver = ScopeResolver(_enabled_flags())
            with pytest.raises(ScopeNotFoundError):
                resolver.resolve_in_transaction(
                    connection, principal, ids[space_key], Capability.READ
                )
            listed = resolver.list_selectable_in_transaction(connection, principal)
            assert ids[space_key] not in {item.id for item in listed.items}
            assert all(item.kind != kind or item.id != ids[space_key] for item in listed.items)
            after = connection.execute(
                """SELECT
                     (SELECT count(*) FROM knowledge_space),
                     (SELECT count(*) FROM audit_event),
                     (SELECT count(*) FROM outbox_event)"""
            ).fetchone()
            assert tuple(before) == tuple(after)
    finally:
        connection.close()


@pytest.mark.scenario("ORG-003")
def test_current_part_and_open_membership_mismatch_fails_internal(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    ids = _seed(factory)
    connection = factory.open()
    try:
        with transaction(connection):
            # Closing a non-leader's open membership leaves a transaction-local
            # mismatch until the organization service completes the companion
            # user update.  The resolver must detect that snapshot immediately.
            membership = OrganizationRepository().get_current_membership(
                connection, "admin"
            )
            assert membership is not None
            OrganizationRepository().close_current_membership(
                connection,
                user_id="admin",
                expected_membership_id=membership.id,
                closed_at_ms=4000,
            )
            principal = _principal(connection, "admin")
            with pytest.raises(ScopeIntegrityError) as caught:
                ScopeResolver(_enabled_flags()).list_selectable_in_transaction(
                    connection, principal
                )
            assert caught.value.code == "INTERNAL_ERROR"
            assert caught.value.http_status == 500
    finally:
        connection.close()


@pytest.mark.scenario("ORG-005")
@pytest.mark.scenario("SPACE-003")
def test_epoch_change_immediately_rejects_old_principal(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    ids = _seed(factory)
    connection = factory.open()
    try:
        with transaction(connection, mode=TransactionMode.DEFERRED):
            old_principal = _principal(connection, "admin")
        with transaction(connection):
            membership = OrganizationRepository().get_current_membership(
                connection, "admin"
            )
            state = OrganizationRepository().get_user_organization_state(
                connection, "admin"
            )
            assert membership is not None and state is not None
            OrganizationRepository().close_current_membership(
                connection,
                user_id="admin",
                expected_membership_id=membership.id,
                closed_at_ms=5000,
            )
            OrganizationRepository().open_membership(
                connection,
                membership_id=_uuid("membership:admin:moved"),
                user_id="admin",
                part_id=ids["part_b"],
                assigned_by="admin",
                reason="Move for stale-scope proof",
                valid_from_ms=5000,
            )
            OrganizationRepository().compare_and_swap_user_part(
                connection,
                user_id="admin",
                expected_current_part_id=ids["part_a"],
                target_part_id=ids["part_b"],
                expected_scope_epoch=state.scope_epoch,
                expected_row_version=state.row_version,
                updated_at_ms=5000,
            )
        with transaction(connection, mode=TransactionMode.DEFERRED):
            with pytest.raises(ScopeStaleError) as caught:
                ScopeResolver(_enabled_flags()).resolve_in_transaction(
                    connection, old_principal, ids["part_space_a"], Capability.READ
                )
            assert caught.value.code == "SCOPE_STALE"
            assert caught.value.http_status == 409
    finally:
        connection.close()


@pytest.mark.scenario("ORG-006")
def test_write_disabled_space_has_read_entitlement_only_and_rollout_intersects(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    ids = _seed(factory)
    connection = factory.open()
    try:
        with transaction(connection, mode=TransactionMode.DEFERRED):
            principal = _principal(connection, "member")
            read_scope = ScopeResolver(_enabled_flags()).resolve_in_transaction(
                connection, principal, ids["part_space_a"], Capability.READ
            )
            assert read_scope.entitled_capabilities == (Capability.READ,)
            assert read_scope.effective_capabilities == (Capability.READ,)
            with pytest.raises(ScopeForbiddenError):
                ScopeResolver(_enabled_flags()).resolve_in_transaction(
                    connection, principal, ids["part_space_a"], Capability.EDIT
                )
        with transaction(connection):
            _set_write(connection, ids["part_space_a"], enabled=True)
        with transaction(connection, mode=TransactionMode.DEFERRED):
            principal = _principal(connection, "member")
            read_only = ScopeResolver(
                _enabled_flags(part_writes=False)
            ).resolve_in_transaction(
                connection, principal, ids["part_space_a"], Capability.READ
            )
            assert read_only.entitled_capabilities == _PART_LEADER_CAPABILITIES
            assert read_only.effective_capabilities == (Capability.READ,)
            with pytest.raises(ScopeForbiddenError):
                ScopeResolver(_enabled_flags(part_writes=False)).resolve_in_transaction(
                    connection, principal, ids["part_space_a"], Capability.EDIT
                )
    finally:
        connection.close()


def test_default_all_off_lists_none_and_forbids_direct_access(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    ids = _seed(factory)
    connection = factory.open()
    try:
        with transaction(connection, mode=TransactionMode.DEFERRED):
            principal = _principal(connection, "member")
            resolver = ScopeResolver()
            assert resolver.list_selectable_in_transaction(connection, principal).items == ()
            with pytest.raises(ScopeForbiddenError):
                resolver.resolve_in_transaction(
                    connection, principal, ids["personal_member"], Capability.READ
                )
    finally:
        connection.close()


def test_disabled_missing_and_malformed_principals_are_stable_errors(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    ids = _seed(factory)
    connection = factory.open()
    try:
        with transaction(connection, mode=TransactionMode.DEFERRED):
            resolver = ScopeResolver(_enabled_flags())
            for principal in (
                SessionPrincipal("disabled", 1),
                SessionPrincipal("missing", 1),
            ):
                with pytest.raises(ScopeAuthenticationRequiredError) as caught:
                    resolver.resolve_in_transaction(
                        connection, principal, ids["personal_disabled"], Capability.READ
                    )
                assert caught.value.http_status == 401
                assert caught.value.retryable is False
                assert caught.value.as_error()["code"] == "AUTHENTICATION_REQUIRED"
            with pytest.raises(ScopeValidationError):
                resolver.list_selectable_in_transaction(
                    connection, SessionPrincipal("Bad User", 1)
                )
            with pytest.raises(ScopeValidationError):
                resolver.list_selectable_in_transaction(
                    connection, SessionPrincipal("member", 0)
                )
    finally:
        connection.close()


def test_resolver_requires_active_caller_owned_transaction_and_never_lazy_creates(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    ids = _seed(factory)
    connection = factory.open()
    try:
        principal = SessionPrincipal("member", 2)
        with pytest.raises(KnowledgeCoreTransactionError):
            ScopeResolver(_enabled_flags()).resolve_in_transaction(
                connection, principal, ids["personal_member"], Capability.READ
            )
        with transaction(connection, mode=TransactionMode.DEFERRED):
            before = connection.execute(
                """SELECT
                     (SELECT count(*) FROM knowledge_space),
                     (SELECT count(*) FROM audit_event),
                     (SELECT count(*) FROM outbox_event)"""
            ).fetchone()
            with pytest.raises(ScopeNotFoundError):
                ScopeResolver(_enabled_flags()).resolve_in_transaction(
                    connection, principal, _uuid("missing"), Capability.READ
                )
            after = connection.execute(
                """SELECT
                     (SELECT count(*) FROM knowledge_space),
                     (SELECT count(*) FROM audit_event),
                     (SELECT count(*) FROM outbox_event)"""
            ).fetchone()
            assert tuple(before) == tuple(after)
    finally:
        connection.close()


def test_active_user_missing_personal_space_fails_closed_without_partial_list(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    ids = _seed(factory)
    connection = factory.open()
    try:
        with transaction(connection):
            UserRepository().import_legacy(connection, _user("lateuser"))
            state = OrganizationRepository().get_user_organization_state(
                connection, "lateuser"
            )
            assert state is not None
            OrganizationRepository().open_membership(
                connection,
                membership_id=_uuid("membership:lateuser"),
                user_id="lateuser",
                part_id=ids["part_a"],
                assigned_by="admin",
                reason="Late user intentionally not provisioned",
                valid_from_ms=6000,
            )
            OrganizationRepository().compare_and_swap_user_part(
                connection,
                user_id="lateuser",
                expected_current_part_id=None,
                target_part_id=ids["part_a"],
                expected_scope_epoch=state.scope_epoch,
                expected_row_version=state.row_version,
                updated_at_ms=6000,
            )
        with transaction(connection, mode=TransactionMode.DEFERRED):
            principal = _principal(connection, "lateuser")
            before = connection.execute("SELECT count(*) FROM knowledge_space").fetchone()[0]
            with pytest.raises(ScopeIntegrityError):
                ScopeResolver(_enabled_flags()).list_selectable_in_transaction(
                    connection, principal
                )
            assert connection.execute("SELECT count(*) FROM knowledge_space").fetchone()[0] == before
    finally:
        connection.close()


@pytest.mark.parametrize(
    "flags",
    (
        FeatureFlags(personal_reads=True),
        FeatureFlags(core_enabled=True, personal_writes=True),
        FeatureFlags(core_enabled=True, part_writes=True),
    ),
)
def test_invalid_direct_rollout_snapshot_dependency_is_rejected(flags) -> None:
    with pytest.raises(ValueError):
        ScopeResolver(flags)


@pytest.mark.scenario("HSPACE-010")
def test_capability_order_and_public_error_contract_are_frozen() -> None:
    assert tuple(value.value for value in Capability) == (
        "read",
        "publish",
        "edit",
        "deactivate",
        "restore",
        "curate",
        "temporal_mutate",
        "local_edge_correct",
        "aggregate_edge_correct",
        "distribution_grant",
        "distribution_revoke",
        "force_release",
        "purge_eligible",
    )
    expected = {
        ScopeValidationError: ("VALIDATION_ERROR", 400),
        ScopeAuthenticationRequiredError: ("AUTHENTICATION_REQUIRED", 401),
        ScopeStaleError: ("SCOPE_STALE", 409),
        ScopeNotFoundError: ("NOT_FOUND", 404),
        ScopeForbiddenError: ("FORBIDDEN", 403),
        ScopeIntegrityError: ("INTERNAL_ERROR", 500),
    }
    for error_type, (code, status) in expected.items():
        error = error_type("safe")
        assert isinstance(error, KnowledgeCoreError)
        assert error.code == code
        assert error.http_status == status
        assert error.retryable is False
        assert error.as_error() == {
            "code": code,
            "message": "safe",
            "retryable": False,
        }


def test_scope_module_fresh_import_is_inert_and_framework_independent(tmp_path) -> None:
    script = r'''
import importlib
import json
from pathlib import Path
import sys
import threading

before_files = tuple(sorted(str(path.relative_to(Path.cwd())) for path in Path.cwd().rglob('*')))
before_threads = tuple(thread.name for thread in threading.enumerate())
scope = importlib.import_module('code_assist_v1.knowledge_core.scope')
after_files = tuple(sorted(str(path.relative_to(Path.cwd())) for path in Path.cwd().rglob('*')))
after_threads = tuple(thread.name for thread in threading.enumerate())
blocked = tuple(name for name in (
    'flask',
    'code_assist_v1.app_code',
    'code_assist_v1.app_notion',
    'code_assist_v1.auth',
    'code_assist_v1.config',
    'code_assist_v1.notion_store',
    'code_assist_v1.second_brain_store',
) if name in sys.modules)
print(json.dumps({
    'blocked': blocked,
    'capabilities': [value.value for value in scope.Capability],
    'files_unchanged': before_files == after_files,
    'threads_unchanged': before_threads == after_threads,
}, ensure_ascii=True, sort_keys=True))
'''
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == {
        "blocked": [],
        "capabilities": [value.value for value in Capability],
        "files_unchanged": True,
        "threads_unchanged": True,
    }
    assert list(tmp_path.iterdir()) == []

from __future__ import annotations

from dataclasses import replace
from dataclasses import dataclass
import hashlib
from uuid import UUID, uuid5

import pytest

from code_assist_v1.knowledge_core.db import ConnectionFactory, transaction
from code_assist_v1.knowledge_core.domain import FeatureFlags
from code_assist_v1.knowledge_core.migrations import (
    CANONICAL_MIGRATION_CATALOG,
    MigrationStorageLayout,
    SingleMigrator,
)
from code_assist_v1.knowledge_core.migrations.v001_seed import RESPONSIBILITY_ROOT_ID
from code_assist_v1.knowledge_core.repositories.artifacts import StagedVerifiedBlob
from code_assist_v1.knowledge_core.repositories.findings import FindingDetection
from code_assist_v1.knowledge_core.repositories.higher_spaces import HigherSpaceRepository
from code_assist_v1.knowledge_core.repositories.organization import OrganizationRepository
from code_assist_v1.knowledge_core.repositories.publication_authority import (
    HigherPublicationTargetDescriptor,
)
from code_assist_v1.knowledge_core.repositories.spaces import SpaceRepository
from code_assist_v1.knowledge_core.repositories.users import LegacyUserRecord, UserRepository
from code_assist_v1.knowledge_core.scope import ScopeResolver, SessionPrincipal
from code_assist_v1.knowledge_core.services.findings import (
    FindingAuthorizationError,
    FindingNotFoundError,
    RecordFindingCommand,
    ReviewFindingCommand,
    VersionedFindingService,
)
from code_assist_v1.knowledge_core.services.publication import (
    CreateManualPreviewCommand,
    CreateUploadPreviewCommand,
    GetPublicationPreviewQuery,
    PublicationPreviewAuthorizationError,
    PublicationPreviewService,
    PublicationService,
    PublishPreviewCommand,
)
from code_assist_v1.knowledge_core.services.higher_authority import (
    HigherAuthorityService,
    SetHigherRoleCommand,
)
from code_assist_v1.tests.knowledge_spaces.unit.test_publication_service import (
    _run_publication_processes,
)


pytestmark = pytest.mark.unit
_NAMESPACE = UUID("f899ba3b-f7ea-4a28-aa52-0efda47f069f")
_CURSOR_SECRET = b"higher-publication-service-test-secret"


def _uuid(label: str) -> str:
    return str(uuid5(_NAMESPACE, label))


def _factory(layout) -> ConnectionFactory:
    factory = ConnectionFactory._for_isolated_tests(
        layout.core_db_path, CANONICAL_MIGRATION_CATALOG
    )
    SingleMigrator(
        factory,
        app_version="higher-publication-service-test",
        storage_layout=MigrationStorageLayout(
            data_root=layout.root,
            database=layout.core_db_path,
            blob_root=layout.blobs_dir,
            projection_root=layout.projections_dir,
        ),
    ).run()
    return factory


def _legacy_user(user_id: str, *, admin: bool = False) -> LegacyUserRecord:
    return LegacyUserRecord(
        id=user_id,
        display_name=user_id.title(),
        password_hash=f"scrypt:test:{user_id}",
        system_role="admin" if admin else "user",
        account_status="active",
        created_at_ms=100,
        updated_at_ms=100,
    )


def _seed(factory: ConnectionFactory) -> dict[str, str]:
    ids = {
        "team_a": _uuid("org:team:a"),
        "part_a": _uuid("org:part:a"),
        "part_space_a": _uuid("space:part:a"),
    }
    ids["team_space_a"] = ids["team_a"]
    users = UserRepository()
    organizations = OrganizationRepository()
    spaces = SpaceRepository()
    connection = factory.open()
    try:
        with transaction(connection):
            users.import_legacy_many(
                connection,
                (
                    _legacy_user("admin", admin=True),
                    _legacy_user("leader"),
                    _legacy_user("member"),
                ),
            )
            organizations.create_unit(
                connection,
                unit_id=ids["team_a"],
                kind="team",
                parent_id=RESPONSIBILITY_ROOT_ID,
                name="Team A",
                actor_user_id="admin",
                now_ms=1000,
            )
            organizations.create_unit(
                connection,
                unit_id=ids["part_a"],
                kind="part",
                parent_id=ids["team_a"],
                name="Part A",
                actor_user_id="admin",
                now_ms=1000,
            )
            # Higher-role holders and their direct-role replacement candidate
            # deliberately have no Part membership.  V13 models Team leaders
            # and Responsibility executives as direct homes at their own
            # organization level rather than as descendant Part members.
            for user_id in ("admin", "leader", "member"):
                spaces.ensure_personal(
                    connection,
                    space_id=_uuid(f"space:personal:{user_id}"),
                    user_id=user_id,
                    created_at_ms=1200,
                )
            spaces.ensure_part(
                connection,
                space_id=ids["part_space_a"],
                part_id=ids["part_a"],
                created_at_ms=1200,
            )
            connection.execute(
                """INSERT INTO knowledge_space(
                     id,space_kind,owner_user_id,owner_org_unit_id,status,
                     write_enabled,generation,created_at,updated_at,row_version
                   ) VALUES (?,'team',NULL,?,'active',0,0,1200,1200,1)""",
                (ids["team_space_a"], ids["team_a"]),
            )
    finally:
        connection.close()
    return ids


def _epoch(factory: ConnectionFactory, user_id: str) -> int:
    connection = factory.open(read_only=True)
    try:
        return UserRepository().require(connection, user_id).scope_epoch
    finally:
        connection.close()


def _higher_service(factory: ConnectionFactory) -> HigherAuthorityService:
    return HigherAuthorityService(
        factory, _scope(), cursor_secret=_CURSOR_SECRET
    )


def _role_command(
    factory: ConnectionFactory,
    ids: dict[str, str],
    *,
    operation: str,
    assignee: str,
) -> SetHigherRoleCommand:
    return SetHigherRoleCommand(
        actor_user_id="admin",
        actor_scope_epoch=_epoch(factory, "admin"),
        operation_id=_uuid(operation),
        request_id=_uuid(f"request:{operation}"),
        org_unit_id=ids["team_a"],
        role="team_leader",
        assignee_user_id=assignee,
        expected_current_assignment_id=None,
        expected_current_assignment_generation=None,
        expected_current_assignment_row_version=None,
        expected_space_row_version=1,
        enable_writes=True,
        reason="higher publication fixture role",
    )


def _open_team_role(factory: ConnectionFactory, ids: dict[str, str]):
    connection = factory.open(read_only=True)
    try:
        return HigherSpaceRepository().get_open_role(
            connection, org_unit_id=ids["team_a"], role="team_leader"
        )
    finally:
        connection.close()


def _space(factory: ConnectionFactory, space_id: str):
    connection = factory.open(read_only=True)
    try:
        return SpaceRepository().require_by_id(connection, space_id)
    finally:
        connection.close()


@dataclass(slots=True)
class _Lease:
    closed: bool = False

    def close(self) -> None:
        self.closed = True


@dataclass(frozen=True, slots=True)
class _Observation:
    blob: StagedVerifiedBlob
    lease: _Lease


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
        assert hashlib.sha256(content).hexdigest() == expected_sha256
        return _Observation(
            StagedVerifiedBlob(
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
            _Lease(),
        )


def _scope() -> ScopeResolver:
    return ScopeResolver(
        FeatureFlags(
            core_enabled=True,
            personal_reads=True,
            part_reads=True,
            higher_spaces=True,
            team_writes=True,
            responsibility_writes=True,
        )
    )


def _descriptor(factory, ids: dict[str, str]) -> HigherPublicationTargetDescriptor:
    role = _open_team_role(factory, ids)
    assert role is not None
    return HigherPublicationTargetDescriptor(
        space_id=ids["team_space_a"],
        kind="team",
        capability="publish",
        role_assignment_id=role.id,
        role_assignment_generation=role.assignment_generation,
    )


def _preview_command(
    factory,
    ids: dict[str, str],
    *,
    actor: str = "leader",
    label: str = "team-publication",
) -> CreateManualPreviewCommand:
    connection = factory.open(read_only=True)
    try:
        epoch = int(
            connection.execute(
                "SELECT scope_epoch FROM app_user WHERE id=?", (actor,)
            ).fetchone()[0]
        )
    finally:
        connection.close()
    return CreateManualPreviewCommand(
        request_id=_uuid(f"{label}:preview-request"),
        operation_id=_uuid(f"{label}:preview-operation"),
        actor_user_id=actor,
        actor_scope_epoch=epoch,
        markdown="# Team authority\n\nExact higher-space publication.\n",
        target=_descriptor(factory, ids),
    )


@pytest.mark.scenario("HSPACE-009")
@pytest.mark.scenario("HSPACE-010")
@pytest.mark.scenario("HSPACE-011")
def test_team_leader_preview_commit_and_exact_replay_are_authority_fenced(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    ids = _seed(factory)
    _higher_service(factory).set_role(
        _role_command(factory, ids, operation="publish-role", assignee="leader")
    )
    command = _preview_command(factory, ids)
    previews = PublicationPreviewService(factory, _scope(), _Stager())
    preview = previews.create_manual_preview(command)
    document = previews.get_preview(
        GetPublicationPreviewQuery(
            actor_user_id=command.actor_user_id,
            actor_scope_epoch=command.actor_scope_epoch,
            preview_id=preview.preview_id,
        )
    )
    assert document["available_target_modes"] == ["explicit"]
    assert document["available_targets"] == [command.target.to_mapping()]

    publish = PublishPreviewCommand(
        request_id=_uuid("team-publication:commit-request"),
        operation_id=_uuid("team-publication:commit-operation"),
        actor_user_id=command.actor_user_id,
        actor_scope_epoch=command.actor_scope_epoch,
        preview_id=preview.preview_id,
        source_snapshot_sha256=preview.source_snapshot_sha256,
        target_mode="explicit",
        target=command.target,
    )
    service = PublicationService(factory, _scope())
    first = service.publish(publish)
    replay = service.publish(
        replace(publish, request_id=_uuid("team-publication:replay-request"))
    )
    assert replay == first
    assert first.target == command.target
    assert first.targets[0]["space_id"] == ids["team_space_a"]

    connection = factory.open(read_only=True)
    try:
        assert connection.execute(
            "SELECT count(*) FROM higher_publication_preview_authority"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT count(*) FROM higher_publication_operation_authority"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT count(*) FROM knowledge_item WHERE space_id=?",
            (ids["team_space_a"],),
        ).fetchone()[0] == 1
    finally:
        connection.close()


def test_team_leader_can_commit_concurrent_previews_after_corpus_growth(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    ids = _seed(factory)
    _higher_service(factory).set_role(
        _role_command(factory, ids, operation="parallel-role", assignee="leader")
    )
    previews = PublicationPreviewService(factory, _scope(), _Stager())
    first_command = replace(
        _preview_command(factory, ids, label="parallel-first"),
        markdown="# Parallel Team document one\n\nFirst body.\n",
    )
    second_command = replace(
        _preview_command(factory, ids, label="parallel-second"),
        markdown="# Parallel Team document two\n\nSecond body.\n",
    )
    first_preview = previews.create_manual_preview(first_command)
    second_preview = previews.create_manual_preview(second_command)
    service = PublicationService(factory, _scope())

    first = service.publish(
        PublishPreviewCommand(
            request_id=_uuid("parallel-first:commit-request"),
            operation_id=_uuid("parallel-first:commit-operation"),
            actor_user_id="leader",
            actor_scope_epoch=first_command.actor_scope_epoch,
            preview_id=first_preview.preview_id,
            source_snapshot_sha256=first_preview.source_snapshot_sha256,
            target_mode="explicit",
            target=first_command.target,
        )
    )
    second = service.publish(
        PublishPreviewCommand(
            request_id=_uuid("parallel-second:commit-request"),
            operation_id=_uuid("parallel-second:commit-operation"),
            actor_user_id="leader",
            actor_scope_epoch=second_command.actor_scope_epoch,
            preview_id=second_preview.preview_id,
            source_snapshot_sha256=second_preview.source_snapshot_sha256,
            target_mode="explicit",
            target=second_command.target,
        )
    )

    assert first.targets[0]["space_id"] == ids["team_space_a"]
    assert second.targets[0]["space_id"] == ids["team_space_a"]
    connection = factory.open(read_only=True)
    try:
        assert connection.execute(
            "SELECT count(*) FROM knowledge_item WHERE space_id=?",
            (ids["team_space_a"],),
        ).fetchone()[0] == 2
    finally:
        connection.close()


@pytest.mark.parametrize("actor", ("member", "admin"))
def test_nonholder_cannot_create_preview_for_a_team_role_descriptor(
    isolated_knowledge_layout, actor: str
) -> None:
    factory = _factory(isolated_knowledge_layout)
    ids = _seed(factory)
    _higher_service(factory).set_role(
        _role_command(factory, ids, operation="denied-role", assignee="leader")
    )
    leader = _preview_command(factory, ids, label="denied")
    nonholder = replace(
        leader,
        actor_user_id=actor,
        actor_scope_epoch=_epoch(factory, actor),
    )
    with pytest.raises(PublicationPreviewAuthorizationError):
        PublicationPreviewService(factory, _scope(), _Stager()).create_manual_preview(
            nonholder
        )


@pytest.mark.scenario("HSPACE-010")
@pytest.mark.scenario("HSPACE-011")
def test_role_replacement_invalidates_an_uncommitted_higher_preview(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    ids = _seed(factory)
    higher = _higher_service(factory)
    higher.set_role(
        _role_command(factory, ids, operation="aba-assign", assignee="leader")
    )
    preview_command = _preview_command(factory, ids, label="aba-preview")
    preview = PublicationPreviewService(factory, _scope(), _Stager()).create_manual_preview(
        preview_command
    )
    role = _open_team_role(factory, ids)
    assert role is not None
    space = _space(factory, ids["team_space_a"])
    higher.set_role(
        SetHigherRoleCommand(
            actor_user_id="admin",
            actor_scope_epoch=_epoch(factory, "admin"),
            operation_id=_uuid("aba-replace"),
            request_id=_uuid("request:aba-replace"),
            org_unit_id=ids["team_a"],
            role="team_leader",
            assignee_user_id="member",
            expected_current_assignment_id=role.id,
            expected_current_assignment_generation=role.assignment_generation,
            expected_current_assignment_row_version=role.row_version,
            expected_space_row_version=space.row_version,
            enable_writes=True,
            reason="replace the role episode before commit",
        )
    )
    with pytest.raises(PublicationPreviewAuthorizationError):
        PublicationService(factory, _scope()).publish(
            PublishPreviewCommand(
                request_id=_uuid("aba-commit-request"),
                operation_id=_uuid("aba-commit-operation"),
                actor_user_id="leader",
                actor_scope_epoch=preview_command.actor_scope_epoch,
                preview_id=preview.preview_id,
                source_snapshot_sha256=preview.source_snapshot_sha256,
                target_mode="explicit",
                target=preview_command.target,
            )
        )

    connection = factory.open(read_only=True)
    try:
        assert connection.execute(
            "SELECT count(*) FROM publication_operation"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT status FROM publication_preview WHERE id=?",
            (preview.preview_id,),
        ).fetchone()[0] == "ready"
    finally:
        connection.close()


@pytest.mark.scenario("HSPACE-010")
@pytest.mark.scenario("HSPACE-011")
def test_role_end_and_write_disable_invalidate_an_uncommitted_higher_preview(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    ids = _seed(factory)
    higher = _higher_service(factory)
    assigned = higher.set_role(
        _role_command(factory, ids, operation="disable-assign", assignee="leader")
    )
    preview_command = _preview_command(factory, ids, label="disable-preview")
    preview = PublicationPreviewService(factory, _scope(), _Stager()).create_manual_preview(
        preview_command
    )
    higher.set_role(
        SetHigherRoleCommand(
            actor_user_id="admin",
            actor_scope_epoch=_epoch(factory, "admin"),
            operation_id=_uuid("disable-end"),
            request_id=_uuid("request:disable-end"),
            org_unit_id=ids["team_a"],
            role="team_leader",
            assignee_user_id=None,
            expected_current_assignment_id=assigned.assignment_id,
            expected_current_assignment_generation=assigned.assignment_generation,
            expected_current_assignment_row_version=assigned.assignment_row_version,
            expected_space_row_version=assigned.space_row_version,
            enable_writes=False,
            reason="end the role episode and disable writes before commit",
        )
    )

    with pytest.raises(PublicationPreviewAuthorizationError):
        PublicationService(factory, _scope()).publish(
            PublishPreviewCommand(
                request_id=_uuid("disable-commit-request"),
                operation_id=_uuid("disable-commit-operation"),
                actor_user_id="leader",
                actor_scope_epoch=preview_command.actor_scope_epoch,
                preview_id=preview.preview_id,
                source_snapshot_sha256=preview.source_snapshot_sha256,
                target_mode="explicit",
                target=preview_command.target,
            )
        )

    connection = factory.open(read_only=True)
    try:
        assert connection.execute(
            "SELECT count(*) FROM publication_operation"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT status FROM publication_preview WHERE id=?",
            (preview.preview_id,),
        ).fetchone()[0] == "ready"
        space = SpaceRepository().require_by_id(connection, ids["team_space_a"])
        assert space.write_enabled is False
    finally:
        connection.close()


@pytest.mark.scenario("HSPACE-009")
@pytest.mark.scenario("HSPACE-010")
@pytest.mark.parametrize("unauthorized_actor", ("member", "admin"))
def test_team_leader_can_review_native_team_finding_but_nonholder_cannot(
    isolated_knowledge_layout, unauthorized_actor: str
) -> None:
    factory = _factory(isolated_knowledge_layout)
    ids = _seed(factory)
    _higher_service(factory).set_role(
        _role_command(factory, ids, operation="curation-role", assignee="leader")
    )
    preview_command = _preview_command(factory, ids, label="curation-preview")
    preview = PublicationPreviewService(factory, _scope(), _Stager()).create_manual_preview(
        preview_command
    )
    published = PublicationService(factory, _scope()).publish(
        PublishPreviewCommand(
            request_id=_uuid("curation-commit-request"),
            operation_id=_uuid("curation-commit-operation"),
            actor_user_id="leader",
            actor_scope_epoch=preview_command.actor_scope_epoch,
            preview_id=preview.preview_id,
            source_snapshot_sha256=preview.source_snapshot_sha256,
            target_mode="explicit",
            target=preview_command.target,
        )
    )
    target = published.targets[0]
    findings = VersionedFindingService(factory, _scope())
    finding = findings.record(
        RecordFindingCommand(
            FindingDetection(
                space_id=ids["team_space_a"],
                finding_kind="curation_release",
                severity="info",
                policy_variant=None,
                source_item_id=target["item_id"],  # type: ignore[arg-type]
                source_revision_id=target["resolved_revision_id"],  # type: ignore[arg-type]
                target_item_id=None,
                target_revision_id=None,
                detector_name="higher_publication_test",
                detector_version="v1",
                rule_version="higher-publication-test-v1",
                model_id=None,
                prompt_version=None,
                confidence=None,
                detector_evidence_sha256="d" * 64,
                candidate_retrieval_evidence_sha256=None,
                evidence_codes=("ready_for_team_release",),
                evidence_spans=(),
                candidate_snapshot=None,
                source_markdown=None,
                target_markdown=None,
            ),
            _uuid("curation-finding-request"),
        )
    ).finding
    # A descendant member can see the aggregate but lacks curate; an unrelated
    # administrator is concealed at the scope boundary.  Neither is granted
    # higher authorship merely by membership or system administration.
    with pytest.raises((FindingAuthorizationError, FindingNotFoundError)):
        findings.review(
            ReviewFindingCommand(
                principal=SessionPrincipal(
                    unauthorized_actor, _epoch(factory, unauthorized_actor)
                ),
                finding_id=finding.id,
                expected_row_version=1,
                decision="approve",
                reason="a nonholder must not curate native Team knowledge",
                operation_id=_uuid(f"{unauthorized_actor}-curation-operation"),
                request_id=_uuid(f"{unauthorized_actor}-curation-request"),
                space_id=ids["team_space_a"],
            )
        )
    reviewed = findings.review(
        ReviewFindingCommand(
            principal=SessionPrincipal("leader", _epoch(factory, "leader")),
            finding_id=finding.id,
            expected_row_version=1,
            decision="approve",
            reason="current exact Team leader approves the native finding",
            operation_id=_uuid("leader-curation-operation"),
            request_id=_uuid("leader-curation-request"),
            space_id=ids["team_space_a"],
        )
    )
    assert reviewed.outcome == "approved"


@pytest.mark.scenario("HSPACE-011")
def test_higher_upload_job_carries_the_exact_role_and_closure_fence(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    ids = _seed(factory)
    _higher_service(factory).set_role(
        _role_command(factory, ids, operation="upload-role", assignee="leader")
    )
    target = _descriptor(factory, ids)
    epoch = _epoch(factory, "leader")
    queued = PublicationPreviewService(factory, _scope(), _Stager()).create_upload_preview(
        CreateUploadPreviewCommand(
            request_id=_uuid("higher-upload-request"),
            operation_id=_uuid("higher-upload-operation"),
            actor_user_id="leader",
            actor_scope_epoch=epoch,
            content=b"# Higher upload\n\nDurable source.\n",
            original_filename="higher.md",
            media_type="text/markdown",
            target=target,
        )
    )
    connection = factory.open(read_only=True)
    try:
        row = connection.execute(
            """SELECT j.space_id,j.expected_state_json,
                      j.authorization_capabilities_json,a.target_descriptor_json
                 FROM durable_job j
                 JOIN publication_preview p ON p.durable_job_id=j.id
                 JOIN higher_publication_preview_authority a ON a.preview_id=p.id
                WHERE p.id=?""",
            (queued.preview_id,),
        ).fetchone()
        assert row is not None
        import json

        fence = json.loads(row[1])["_authorization_fence"]
        assert row[0] == ids["team_space_a"]
        assert "publish" in json.loads(row[2])
        assert fence["fence_mode"] == "mutation_authority"
        assert fence["native_mutation_space_id"] == ids["team_space_a"]
        assert fence["role_assignment_id"] == target.role_assignment_id
        assert fence["role_assignment_generation"] == target.role_assignment_generation
        assert json.loads(row[3]) == target.to_mapping()
    finally:
        connection.close()


@pytest.mark.scenario("HSPACE-009")
def test_responsibility_executive_can_publish_to_responsibility_native_space(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _seed(factory)
    higher = _higher_service(factory)
    higher.set_role(
        SetHigherRoleCommand(
            actor_user_id="admin",
            actor_scope_epoch=_epoch(factory, "admin"),
            operation_id=_uuid("responsibility-role-operation"),
            request_id=_uuid("responsibility-role-request"),
            org_unit_id=RESPONSIBILITY_ROOT_ID,
            role="responsibility_executive",
            assignee_user_id="leader",
            expected_current_assignment_id=None,
            expected_current_assignment_generation=None,
            expected_current_assignment_row_version=None,
            expected_space_row_version=1,
            enable_writes=True,
            reason="assign the exact Responsibility executive",
        )
    )
    connection = factory.open(read_only=True)
    try:
        role = HigherSpaceRepository().get_open_role(
            connection,
            org_unit_id=RESPONSIBILITY_ROOT_ID,
            role="responsibility_executive",
        )
    finally:
        connection.close()
    assert role is not None
    descriptor = HigherPublicationTargetDescriptor(
        space_id=RESPONSIBILITY_ROOT_ID,
        kind="responsibility",
        capability="publish",
        role_assignment_id=role.id,
        role_assignment_generation=role.assignment_generation,
    )
    epoch = _epoch(factory, "leader")
    preview = PublicationPreviewService(factory, _scope(), _Stager()).create_manual_preview(
        CreateManualPreviewCommand(
            request_id=_uuid("responsibility-preview-request"),
            operation_id=_uuid("responsibility-preview-operation"),
            actor_user_id="leader",
            actor_scope_epoch=epoch,
            markdown="# Responsibility knowledge\n\nExecutive-authored.\n",
            target=descriptor,
        )
    )
    published = PublicationService(factory, _scope()).publish(
        PublishPreviewCommand(
            request_id=_uuid("responsibility-publish-request"),
            operation_id=_uuid("responsibility-publish-operation"),
            actor_user_id="leader",
            actor_scope_epoch=epoch,
            preview_id=preview.preview_id,
            source_snapshot_sha256=preview.source_snapshot_sha256,
            target_mode="explicit",
            target=descriptor,
        )
    )
    assert published.targets[0]["space_id"] == RESPONSIBILITY_ROOT_ID


class _PublicationFailpoint(RuntimeError):
    pass


@pytest.mark.scenario("HSPACE-011")
def test_higher_publication_failpoint_rolls_back_authority_and_domain_effects(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    ids = _seed(factory)
    _higher_service(factory).set_role(
        _role_command(factory, ids, operation="failpoint-role", assignee="leader")
    )
    preview_command = _preview_command(factory, ids, label="failpoint")
    preview = PublicationPreviewService(factory, _scope(), _Stager()).create_manual_preview(
        preview_command
    )
    publish = PublishPreviewCommand(
        request_id=_uuid("failpoint-publish-request"),
        operation_id=_uuid("failpoint-publish-operation"),
        actor_user_id="leader",
        actor_scope_epoch=preview_command.actor_scope_epoch,
        preview_id=preview.preview_id,
        source_snapshot_sha256=preview.source_snapshot_sha256,
        target_mode="explicit",
        target=preview_command.target,
    )

    def failpoint(name: str) -> None:
        if name == "publication.after_operation":
            raise _PublicationFailpoint(name)

    with pytest.raises(_PublicationFailpoint):
        PublicationService(factory, _scope(), failpoint=failpoint).publish(publish)
    connection = factory.open(read_only=True)
    try:
        assert connection.execute(
            "SELECT count(*) FROM publication_operation"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT count(*) FROM higher_publication_operation_authority"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT count(*) FROM knowledge_item WHERE space_id=?",
            (ids["team_space_a"],),
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT status FROM publication_preview WHERE id=?",
            (preview.preview_id,),
        ).fetchone()[0] == "ready"
    finally:
        connection.close()
    committed = PublicationService(factory, _scope()).publish(publish)
    assert committed.targets[0]["space_id"] == ids["team_space_a"]


@pytest.mark.concurrency
@pytest.mark.scenario("HSPACE-011")
def test_two_processes_converge_on_one_exact_higher_publication(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    ids = _seed(factory)
    _higher_service(factory).set_role(
        _role_command(factory, ids, operation="process-role", assignee="leader")
    )
    preview_command = _preview_command(factory, ids, label="process")
    preview = PublicationPreviewService(factory, _scope(), _Stager()).create_manual_preview(
        preview_command
    )
    target = preview_command.target
    assert target is not None
    base: dict[str, object] = {
        "actor_scope_epoch": preview_command.actor_scope_epoch,
        "actor_user_id": "leader",
        "operation_id": _uuid("process-publish-operation"),
        "preview_id": preview.preview_id,
        "request_meta": {"correlation_id": "higher-process-replay"},
        "source_snapshot_sha256": preview.source_snapshot_sha256,
        "target_mode": "explicit",
        "target": target.to_mapping(),
    }
    results = _run_publication_processes(
        isolated_knowledge_layout,
        label="higher-publication-process",
        payloads=tuple(
            {
                **base,
                "request_id": _uuid(f"process-publish-request:{index}"),
            }
            for index in range(2)
        ),
    )
    assert {result["kind"] for result in results} == {"ok"}
    durable = [result["result"] for result in results]
    assert durable[0] == durable[1]
    connection = factory.open(read_only=True)
    try:
        assert connection.execute(
            "SELECT count(*) FROM publication_operation"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT count(*) FROM higher_publication_operation_authority"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT count(*) FROM knowledge_item WHERE space_id=?",
            (ids["team_space_a"],),
        ).fetchone()[0] == 1
    finally:
        connection.close()

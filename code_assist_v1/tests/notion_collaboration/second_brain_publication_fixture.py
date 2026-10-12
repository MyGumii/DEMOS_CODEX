from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import sqlite3
from uuid import UUID, uuid5

from code_assist_v1 import notion_workspace_collaboration_migration as canonical_migration
from code_assist_v1 import notion_workspace_editing_authority_migration as editing_migration
from code_assist_v1 import notion_workspace_iam_migration as iam_migration
from code_assist_v1 import notion_workspace_live_collaboration_migration as live_migration
from code_assist_v1 import notion_workspace_realtime_transport_migration as transport_migration
from code_assist_v1 import notion_workspace_second_brain_publication_migration as publication_migration
from code_assist_v1 import notion_workspace_store as workspace_store
from code_assist_v1.knowledge_core.db import ConnectionFactory, transaction
from code_assist_v1.knowledge_core.domain import FeatureFlags
from code_assist_v1.knowledge_core.migrations import (
    CANONICAL_MIGRATION_CATALOG,
    MigrationStorageLayout,
    SingleMigrator,
)
from code_assist_v1.knowledge_core.repositories.artifacts import StagedVerifiedBlob
from code_assist_v1.knowledge_core.repositories.organization import (
    OrganizationRepository,
    RESPONSIBILITY_ROOT_ID,
)
from code_assist_v1.knowledge_core.repositories.spaces import SpaceRepository
from code_assist_v1.knowledge_core.repositories.users import (
    LegacyUserRecord,
    UserRepository,
)
from code_assist_v1.knowledge_core.scope import ScopeResolver
from code_assist_v1.knowledge_core.services.publication import (
    PublicationPreviewService,
    PublicationService,
)
from code_assist_v1.knowledge_core.services.spaces import space_id_for_owner
from code_assist_v1.notion_collaboration.iam_authority import InMemoryIdentityDirectory
from code_assist_v1.notion_collaboration.live_authority import CollaborativeLiveAuthority
from code_assist_v1.notion_collaboration.second_brain_boundary import (
    CollaborativeNotionSnapshotProvider,
)
from code_assist_v1.notion_collaboration.second_brain_publication import (
    SecondBrainPublicationCoordinator,
)


SUBJECTS = ("owner", "editor", "outsider")
ACTOR = "editor"
NAMESPACE = UUID("e576c8a0-b42f-4522-8d19-9f84531de66c")


def uid(label: str) -> str:
    return str(uuid5(NAMESPACE, label))


def _backup(source: Path, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_con = sqlite3.connect(source)
    target_con = sqlite3.connect(destination)
    try:
        source_con.backup(target_con)
        target_con.commit()
    finally:
        target_con.close()
        source_con.close()
    return destination


def _apply(migration, catalog: Path, root: Path, label: str) -> None:
    backup = _backup(catalog, root / "backups" / label)
    plan = migration.build_plan(catalog, root)
    migration.apply_plan(
        catalog,
        root,
        exact_plan_checksum=plan["plan_checksum"],
        backup_confirmed=True,
        catalog_quiesced_confirmed=True,
        backup_path=backup,
    )


class _Lease:
    def close(self) -> None:
        return None


@dataclass(frozen=True)
class _Observation:
    blob: StagedVerifiedBlob
    lease: _Lease


class MemoryStager:
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
        relative_path = (
            f"{expected_sha256[:2]}/{expected_sha256[2:4]}/"
            f"{expected_sha256}"
        )
        return _Observation(
            StagedVerifiedBlob(
                sha256=expected_sha256,
                size_bytes=len(content),
                media_type=media_type,
                relative_path=relative_path,
                created_at_ms=effective_at_ms,
                verified_at_ms=effective_at_ms,
            ),
            _Lease(),
        )


def sbe_flags() -> FeatureFlags:
    return FeatureFlags(
        core_enabled=True,
        personal_reads=True,
        personal_writes=True,
        part_reads=True,
        part_writes=True,
    )


def open_sbe_factory(database: Path) -> ConnectionFactory:
    return ConnectionFactory._for_isolated_tests(database, CANONICAL_MIGRATION_CATALOG)


def build_services(
    notion_catalog: Path,
    notion_root: Path,
    sbe_database: Path,
    *,
    failpoint=None,
):
    identities = InMemoryIdentityDirectory(SUBJECTS)
    provider = CollaborativeNotionSnapshotProvider.open(
        notion_catalog, notion_root, identities
    )
    factory = open_sbe_factory(sbe_database)
    scope = ScopeResolver(sbe_flags())
    previews = PublicationPreviewService(
        factory,
        scope,
        MemoryStager(),
        notion_snapshots=provider,
    )
    publications = PublicationService(factory, scope)
    coordinator = SecondBrainPublicationCoordinator(
        provider,
        previews,
        publications,
        failpoint=failpoint,
    )
    return provider, factory, coordinator


def _seed_sbe(layout) -> tuple[ConnectionFactory, int, str, str]:
    factory = open_sbe_factory(layout.core_db_path)
    SingleMigrator(
        factory,
        app_version="s0a-p2-publication-fence-test",
        storage_layout=MigrationStorageLayout(
            data_root=layout.root,
            database=layout.core_db_path,
            blob_root=layout.blobs_dir,
            projection_root=layout.projections_dir,
        ),
    ).run()
    personal_space_id = space_id_for_owner("personal", ACTOR)
    team_id = uid("team")
    part_id = uid("part")
    part_space_id = uid("part-space")
    con = factory.open()
    try:
        with transaction(con):
            users = UserRepository()
            users.import_legacy(
                con,
                LegacyUserRecord(
                    id=ACTOR,
                    display_name="Publication Editor",
                    password_hash="scrypt:s0a-p2:editor",
                    system_role="user",
                    account_status="active",
                    created_at_ms=100,
                    updated_at_ms=100,
                ),
            )
            spaces = SpaceRepository()
            personal = spaces.ensure_personal(
                con,
                space_id=personal_space_id,
                user_id=ACTOR,
                created_at_ms=100,
            )
            spaces.update_state(
                con,
                space_id=personal_space_id,
                expected_row_version=personal.space.row_version,
                status="active",
                write_enabled=True,
                updated_at_ms=101,
            )
            organization = OrganizationRepository()
            organization.create_unit(
                con,
                unit_id=team_id,
                kind="team",
                parent_id=RESPONSIBILITY_ROOT_ID,
                name="S0A Team",
                actor_user_id=ACTOR,
                now_ms=110,
            )
            organization.create_unit(
                con,
                unit_id=part_id,
                kind="part",
                parent_id=team_id,
                name="S0A Part",
                actor_user_id=ACTOR,
                now_ms=111,
            )
            organization.open_membership(
                con,
                membership_id=uid("membership"),
                user_id=ACTOR,
                part_id=part_id,
                assigned_by=ACTOR,
                reason="S0A-P2 fixture",
                valid_from_ms=112,
            )
            current = users.require(con, ACTOR)
            moved = organization.compare_and_swap_user_part(
                con,
                user_id=ACTOR,
                expected_current_part_id=None,
                target_part_id=part_id,
                expected_scope_epoch=current.scope_epoch,
                expected_row_version=current.row_version,
                updated_at_ms=113,
            )
            organization.open_part_leader(
                con,
                assignment_id=uid("part-leader"),
                user_id=ACTOR,
                part_id=part_id,
                assigned_by=ACTOR,
                reason="S0A-P2 fixture",
                valid_from_ms=114,
            )
            part = spaces.ensure_part(
                con,
                space_id=part_space_id,
                part_id=part_id,
                created_at_ms=115,
            )
            spaces.update_state(
                con,
                space_id=part_space_id,
                expected_row_version=part.space.row_version,
                status="active",
                write_enabled=True,
                updated_at_ms=116,
            )
            final_scope_epoch = users.require(con, ACTOR).scope_epoch
    finally:
        con.close()
    return factory, final_scope_epoch, personal_space_id, part_space_id


@dataclass(frozen=True)
class IntegratedEnvironment:
    notion_root: Path
    notion_catalog: Path
    workspace_id: str
    provider: CollaborativeNotionSnapshotProvider
    sbe_factory: ConnectionFactory
    sbe_database: Path
    actor_scope_epoch: int
    personal_space_id: str
    part_space_id: str
    coordinator: SecondBrainPublicationCoordinator


def environment(tmp_path: Path, isolated_knowledge_layout) -> IntegratedEnvironment:
    notion_root = tmp_path / "s0a-p2-notion"
    notion_catalog = Path(
        workspace_store.catalog_path_for_user_data_root(notion_root)
    )
    store = workspace_store.WorkspaceStore(notion_catalog)
    store.bootstrap()
    created = store.create_workspace(
        "owner",
        name="S0A-P2 workspace",
        operation_id="op-s0a-p2-workspace-create-0001",
    )
    workspace_id = str(created["workspace"]["id"])
    store.set_member(
        "owner",
        workspace_id,
        ACTOR,
        role="member",
        base_version=int(created["workspace"]["version"]),
        operation_id="op-s0a-p2-member-set-0001",
    )
    _apply(canonical_migration, notion_catalog, notion_root, "before-2401.db")
    _apply(transport_migration, notion_catalog, notion_root, "before-2402.db")
    _apply(iam_migration, notion_catalog, notion_root, "before-2403.db")
    _apply(live_migration, notion_catalog, notion_root, "before-2404.db")
    live = CollaborativeLiveAuthority.open(
        notion_catalog, notion_root, InMemoryIdentityDirectory(SUBJECTS)
    )
    con = sqlite3.connect(notion_catalog)
    try:
        workspace_epoch = int(
            con.execute(
                "SELECT security_epoch FROM workspaces WHERE id=?", (workspace_id,)
            ).fetchone()[0]
        )
    finally:
        con.close()
    live.create_document(
        "owner",
        workspace_id,
        "page-s0a-p2",
        title="Durable publication",
        blocks=[
            {"id": "heading", "type": "heading_1", "content": "Publication"},
            {"id": "body", "type": "paragraph", "content": "Exact revision body"},
        ],
        expected_workspace_epoch=workspace_epoch,
        operation_id="op-s0a-p2-page-create-0001",
    )
    page = live.read_document("owner", "page-s0a-p2")
    live.set_page_grant(
        "owner",
        "page-s0a-p2",
        "subject",
        ACTOR,
        role="edit_content",
        inherit_to_descendants=False,
        expected_workspace_epoch=int(page["workspace_epoch"]),
        expected_document_epoch=int(page["document_epoch"]),
        operation_id="op-s0a-p2-page-grant-0001",
    )
    _apply(editing_migration, notion_catalog, notion_root, "before-2405.db")
    _apply(publication_migration, notion_catalog, notion_root, "before-2406.db")
    sbe_factory, epoch, personal_id, part_id = _seed_sbe(
        isolated_knowledge_layout
    )
    provider, _factory, coordinator = build_services(
        notion_catalog,
        notion_root,
        isolated_knowledge_layout.core_db_path,
    )
    return IntegratedEnvironment(
        notion_root=notion_root,
        notion_catalog=notion_catalog,
        workspace_id=workspace_id,
        provider=provider,
        sbe_factory=sbe_factory,
        sbe_database=isolated_knowledge_layout.core_db_path,
        actor_scope_epoch=epoch,
        personal_space_id=personal_id,
        part_space_id=part_id,
        coordinator=coordinator,
    )


def sbe_counts(factory: ConnectionFactory) -> dict[str, int]:
    con = factory.open(read_only=True)
    try:
        return {
            name: int(con.execute(f"SELECT count(*) FROM {name}").fetchone()[0])
            for name in (
                "publication_preview",
                "publication_operation",
                "knowledge_item",
                "knowledge_revision",
                "item_source",
            )
        }
    finally:
        con.close()

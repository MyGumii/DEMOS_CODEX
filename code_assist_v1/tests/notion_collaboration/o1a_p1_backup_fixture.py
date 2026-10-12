from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import hashlib
import os
from pathlib import Path
import shutil
import tempfile

from code_assist_v1 import notion_workspace_database_features_migration as database_migration
from code_assist_v1 import notion_workspace_file_authority_migration as file_migration
from code_assist_v1 import notion_workspace_history_authority_migration as history_migration
from code_assist_v1 import notion_workspace_search_authority_migration as search_migration
from code_assist_v1 import notion_workspace_store as workspace_store
from code_assist_v1.knowledge_core.db import transaction
from code_assist_v1.knowledge_core.migrations import (
    CANONICAL_MIGRATION_CATALOG,
    MigrationStorageLayout,
)
from code_assist_v1.knowledge_core.repositories.spaces import SpaceRepository
from code_assist_v1.knowledge_core.services.backup import BackupServiceConfiguration
from code_assist_v1.knowledge_core.services.publication import (
    PublicationPreviewService,
    PublicationService,
)
from code_assist_v1.knowledge_core.scope import ScopeResolver
from code_assist_v1.knowledge_core.migrations.v001_invariants import (
    POSTCONDITION_DEFINITIONS,
)
from code_assist_v1.notion_collaboration.coordinated_workspace_backup import (
    WorkspaceSmokeProbe,
    initialize_isolated_workspace_backup_source,
)
from code_assist_v1.notion_collaboration.file_authority import CollaborativeFileAuthority
from code_assist_v1.notion_collaboration.iam_authority import InMemoryIdentityDirectory
from code_assist_v1.notion_collaboration.search_authority import CollaborativeSearchAuthority
from code_assist_v1.notion_collaboration.second_brain_publication import (
    ApprovePublicationIntentCommand,
    PreparePublicationIntentCommand,
    SecondBrainPublicationCoordinator,
)
from code_assist_v1.tests.notion_collaboration.second_brain_publication_fixture import (
    ACTOR,
    MemoryStager,
    SUBJECTS,
    environment,
    sbe_flags,
    uid,
)
from code_assist_v1.tests.notion_collaboration.test_database_features import _apply


SEARCH_TOKEN = "O1A_RESTORE_TOKEN"
PAGE_ID = "page-s0a-p2"


@dataclass(frozen=True, slots=True)
class O1ABackupEnvironment:
    source_root: Path
    notion_root: Path
    notion_catalog: Path
    sbe_layout: MigrationStorageLayout
    sbe_factory: object
    sbe_configuration: BackupServiceConfiguration
    probe: WorkspaceSmokeProbe


class _MaterializingStager(MemoryStager):
    def __init__(self, blob_root: Path) -> None:
        self._blob_root = Path(blob_root)

    def stage_and_verify(self, content: bytes, **kwargs):
        observation = super().stage_and_verify(content, **kwargs)
        target = self._blob_root / observation.blob.relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            assert target.read_bytes() == content
        else:
            target.write_bytes(content)
        return observation


def build_o1a_backup_environment(
    tmp_path: Path,
    isolated_knowledge_layout,
    *,
    request=None,
) -> O1ABackupEnvironment:
    del tmp_path
    host_temp = Path(os.environ["LOCALAPPDATA"]) / "Temp"
    source_root = Path(tempfile.mkdtemp(prefix="o1a-", dir=host_temp))
    if request is not None:
        request.addfinalizer(lambda: shutil.rmtree(source_root, ignore_errors=True))
    integrated = environment(source_root, isolated_knowledge_layout)

    scope = ScopeResolver(sbe_flags())
    coordinator = SecondBrainPublicationCoordinator(
        integrated.provider,
        PublicationPreviewService(
            integrated.sbe_factory,
            scope,
            _MaterializingStager(isolated_knowledge_layout.blobs_dir),
            notion_snapshots=integrated.provider,
        ),
        PublicationService(integrated.sbe_factory, scope),
    )

    prepared = coordinator.prepare(
        PreparePublicationIntentCommand(
            actor_user_id=ACTOR,
            page_id=PAGE_ID,
            preview_operation_id=uid("o1a:preview"),
        )
    )
    committed = coordinator.approve(
        ApprovePublicationIntentCommand(
            request_id=uid("o1a:request"),
            operation_id=uid("o1a:publish"),
            actor_user_id=ACTOR,
            actor_scope_epoch=integrated.actor_scope_epoch,
            intent_id=prepared.intent_id,
            target_mode="both",
            personal_space_id=integrated.personal_space_id,
        )
    )
    assert committed.publication_result is not None
    item_ids = tuple(
        sorted(str(item["item_id"]) for item in committed.publication_result["targets"])
    )

    sbe_connection = integrated.sbe_factory.open()
    try:
        with transaction(sbe_connection):
            team = sbe_connection.execute(
                "SELECT id FROM org_unit WHERE kind='team' AND status='active'"
            ).fetchone()
            assert team is not None
            SpaceRepository().ensure_higher(
                sbe_connection,
                space_id=uid("o1a:team-space"),
                org_unit_id=str(team["id"]),
                kind="team",
                created_at_ms=117,
            )
    finally:
        sbe_connection.close()

    notion_root = source_root / "notion"
    integrated.notion_root.rename(notion_root)
    notion_catalog = Path(workspace_store.catalog_path_for_user_data_root(notion_root))
    _apply(database_migration, notion_catalog, notion_root, "o1a-before-2407.db")
    _apply(file_migration, notion_catalog, notion_root, "o1a-before-2408.db")
    identities = InMemoryIdentityDirectory(SUBJECTS)
    CollaborativeFileAuthority.open(
        notion_catalog, notion_root, identities, initialize_storage=True
    )
    _apply(history_migration, notion_catalog, notion_root, "o1a-before-2409.db")
    _apply(search_migration, notion_catalog, notion_root, "o1a-before-2410.db")

    authority = CollaborativeSearchAuthority.open(notion_catalog, notion_root, identities)
    page = authority.read_document("owner", PAGE_ID)
    authority.save_document(
        "owner",
        PAGE_ID,
        title="O1A coordinated restore",
        blocks=[
            {
                "id": "o1a-restore-block",
                "position": 0,
                "type": "paragraph",
                "content": f"{SEARCH_TOKEN} canonical content",
                "props": {},
            }
        ],
        expected_revision=int(page["revision"]),
        expected_workspace_epoch=int(page["workspace_epoch"]),
        expected_document_epoch=int(page["document_epoch"]),
        operation_id="op-o1a-save-0001",
    )
    page = authority.read_document("owner", PAGE_ID)
    authority.add_comment(
        "owner",
        PAGE_ID,
        "comment-o1a-restore",
        content="restore comment",
        expected_workspace_epoch=int(page["workspace_epoch"]),
        expected_document_epoch=int(page["document_epoch"]),
        operation_id="op-o1a-comment-0001",
    )
    page = authority.read_document("owner", PAGE_ID)
    authority.create_revision_checkpoint(
        "owner",
        PAGE_ID,
        expected_revision=int(page["revision"]),
        expected_document_sequence=int(page["document_sequence"]),
        expected_workspace_epoch=int(page["workspace_epoch"]),
        expected_document_epoch=int(page["document_epoch"]),
        operation_id="op-o1a-checkpoint-0001",
    )

    payload = b"o1a-p1-restored-file\x00" * 512
    checksum = "sha256:" + hashlib.sha256(payload).hexdigest()
    page = authority.read_document("owner", PAGE_ID)
    upload = authority.begin_upload(
        "owner",
        PAGE_ID,
        original_name="o1a-evidence.bin",
        mime_type="application/octet-stream",
        total_size=len(payload),
        expected_sha256=checksum,
        expected_document_sequence=int(page["document_sequence"]),
        expected_workspace_epoch=int(page["workspace_epoch"]),
        expected_document_epoch=int(page["document_epoch"]),
        operation_id="op-o1a-file-begin-0001",
        chunk_size=65_536,
    )
    authority.stage_chunk(
        "owner",
        str(upload["session_id"]),
        chunk_index=0,
        stream=BytesIO(payload),
        operation_id="op-o1a-file-chunk-0001",
    )
    finalized = authority.finalize_upload(
        "owner",
        str(upload["session_id"]),
        operation_id="op-o1a-file-finalize-0001",
    )
    authority.rebuild_search_index("owner", integrated.workspace_id)

    sbe_layout = MigrationStorageLayout(
        data_root=isolated_knowledge_layout.root,
        database=isolated_knowledge_layout.core_db_path,
        blob_root=isolated_knowledge_layout.blobs_dir,
        projection_root=isolated_knowledge_layout.projections_dir,
    )
    initialize_isolated_workspace_backup_source(
        source_root, sbe_layout=sbe_layout
    )
    sbe_connection = integrated.sbe_factory.open(read_only=True)
    try:
        v1_names = {query: name for name, query in POSTCONDITION_DEFINITIONS}
        search_query = next(
            query
            for name, query in POSTCONDITION_DEFINITIONS
            if name == "search_visibility_consistency"
        )
        violations = []
        for migration in CANONICAL_MIGRATION_CATALOG.migrations:
            for index, query in enumerate(migration.postcondition_queries, start=1):
                if query == search_query:
                    continue
                row = sbe_connection.execute(query).fetchone()
                if row is not None:
                    violations.append(
                        v1_names.get(
                            query, f"v{migration.version:03d}-postcondition-{index}"
                        )
                    )
    finally:
        sbe_connection.close()
    assert violations == []
    return O1ABackupEnvironment(
        source_root=source_root,
        notion_root=notion_root,
        notion_catalog=notion_catalog,
        sbe_layout=sbe_layout,
        sbe_factory=integrated.sbe_factory,
        sbe_configuration=BackupServiceConfiguration(
            app_version="o1a-p1",
            build_revision="b" * 64,
            configuration_version="o1a-local-v1",
            lease_hmac_key_set_version="o1a-test-key-set-v1",
        ),
        probe=WorkspaceSmokeProbe(
            workspace_id=integrated.workspace_id,
            document_id=PAGE_ID,
            owner_subject_id="owner",
            member_subject_id=ACTOR,
            denied_subject_id="outsider",
            search_query=SEARCH_TOKEN,
            file_id=str(finalized["file_id"]),
            second_brain_item_ids=item_ids,
        ),
    )


__all__ = ["O1ABackupEnvironment", "build_o1a_backup_environment"]

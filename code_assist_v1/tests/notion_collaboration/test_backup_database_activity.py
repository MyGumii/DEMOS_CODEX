"""UI-R5: a 2421 activity row survives an isolated coordinated restore."""

import sqlite3
from pathlib import Path

import pytest

from code_assist_v1 import (
    notion_workspace_icon_image_migration as m2411,
    notion_workspace_guest_navigation_migration as m2412,
    notion_workspace_page_archive_migration as m2413,
    notion_workspace_native_page_migration as m2414,
    notion_workspace_member_navigation_migration as m2415,
    notion_workspace_file_copy_migration as m2416,
    notion_workspace_native_team_migration as m2417,
    notion_workspace_row_body_migration as m2418,
    notion_workspace_row_appearance_migration as m2419,
    notion_workspace_same_document_file_copy_migration as m2420,
    notion_workspace_database_activity_migration as m2421,
)
from code_assist_v1.notion_collaboration.coordinated_workspace_backup import (
    LocalGenerationIntegrityError,
    _notion_observation,
    create_local_workspace_generation,
    restore_local_workspace_generation,
    verify_local_workspace_generation,
    verify_local_workspace_restored_root,
)
from code_assist_v1.notion_collaboration.iam_authority import InMemoryIdentityDirectory
from code_assist_v1.notion_collaboration.iam_runtime import CollaborativeWorkspaceStore
from code_assist_v1.notion_workspace_store import catalog_path_for_user_data_root
from code_assist_v1.tests.notion_collaboration.o1a_p1_backup_fixture import (
    PAGE_ID, build_o1a_backup_environment,
)
from code_assist_v1.tests.notion_collaboration.second_brain_publication_fixture import SUBJECTS
from code_assist_v1.tests.notion_collaboration.test_database_features import _apply


@pytest.mark.integration
@pytest.mark.restore
def test_ui_r5_database_activity_survives_2421_coordinated_restore(
    tmp_path, isolated_knowledge_layout, request,
):
    environment = build_o1a_backup_environment(
        tmp_path, isolated_knowledge_layout, request=request,
    )
    for migration in (m2411, m2412, m2413, m2414, m2415, m2416,
                      m2417, m2418, m2419, m2420, m2421):
        _apply(migration, environment.notion_catalog, environment.notion_root,
               f"o1a-before-{migration.TARGET_VERSION}.db")

    store = CollaborativeWorkspaceStore(
        environment.notion_catalog, user_data_root=environment.notion_root,
        identity_directory=InMemoryIdentityDirectory(SUBJECTS),
    )
    page = store.iam.read_document("owner", PAGE_ID)
    store.iam.execute_schema_command(
        "owner", PAGE_ID, database_id="o1a-activity-db",
        command_kind="database.create", target_id="o1a-activity-db",
        payload={"title": "O1A activity database"},
        expected_document_sequence=int(page["document_sequence"]),
        expected_workspace_epoch=int(page["workspace_epoch"]),
        expected_document_epoch=int(page["document_epoch"]),
        expected_target_version=0, operation_id="o1a-activity-create",
    )

    generation = create_local_workspace_generation(
        environment.source_root, "o1a-2421-activity",
        sbe_factory=environment.sbe_factory,
        sbe_layout=environment.sbe_layout,
        sbe_configuration=environment.sbe_configuration,
        smoke_probe=environment.probe,
    )
    manifest = verify_local_workspace_generation(
        environment.source_root, generation.generation_id,
        sbe_factory=environment.sbe_factory,
        sbe_layout=environment.sbe_layout,
    )
    assert manifest["authorities"]["notion"]["observation"]["schema_version"] == m2421.TARGET_VERSION

    restored = restore_local_workspace_generation(
        environment.source_root, generation.generation_id, "restored-2421-activity",
        sbe_factory=environment.sbe_factory,
        sbe_layout=environment.sbe_layout,
        target_parent=isolated_knowledge_layout.root.parent / "o1a-restore-targets",
    )
    receipt = verify_local_workspace_restored_root(restored.target_root)
    assert receipt["notion"]["canonical_match"] is True
    catalog = Path(catalog_path_for_user_data_root(restored.target_root / "notion"))
    con = sqlite3.connect(catalog)
    try:
        assert con.execute("SELECT last_editor_subject_id FROM "
            "workspace_collaboration_database_activity WHERE database_id=?",
            ("o1a-activity-db",)).fetchone() == ("owner",)
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []
        con.execute("UPDATE workspace_schema_migrations SET checksum=? WHERE version=?",
                    ("sha256:" + "0" * 64, m2421.TARGET_VERSION))
        con.commit()
    finally:
        con.close()
    with pytest.raises(LocalGenerationIntegrityError, match="supported exact marker"):
        _notion_observation(catalog)

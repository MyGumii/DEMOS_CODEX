from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest

from code_assist_v1.knowledge_core.errors import KnowledgeCoreDatabasePathError
from code_assist_v1.knowledge_core.migrations.storage_preflight import (
    MigrationStorageLayout,
    verify_migration_storage,
    verify_migration_storage_layout,
)
from code_assist_v1.tools import windows_volume


pytestmark = pytest.mark.scenario("SCHEMA-008")


def _layout(isolated_knowledge_layout) -> MigrationStorageLayout:
    return MigrationStorageLayout(
        data_root=isolated_knowledge_layout.root,
        database=isolated_knowledge_layout.core_db_path,
        blob_root=isolated_knowledge_layout.blobs_dir,
        projection_root=isolated_knowledge_layout.projections_dir,
    )


def _eligible_report(path: str, *, root: str = "C:\\") -> dict[str, object]:
    return {
        "eligible": True,
        "exists": True,
        "reason_codes": [],
        "reparse": {"components": [], "present": False},
        "requested_path": path,
        "resolved_path": path,
        "syntactic_unc": False,
        "volume": {
            "drive_type": "fixed",
            "drive_type_code": 3,
            "filesystem": "NTFS",
            "root": root,
        },
    }


@pytest.mark.unit
def test_p1_3_storage_layout_binds_absent_then_created_database_identity(
    isolated_knowledge_layout,
) -> None:
    layout = _layout(isolated_knowledge_layout)
    before = verify_migration_storage_layout(layout, require_database=False)
    assert before.database_file_identity is None

    raw = sqlite3.connect(layout.database)
    raw.close()
    after = verify_migration_storage_layout(
        layout,
        require_database=True,
        expected=before,
    )

    assert after.database_file_identity is not None
    assert after.database == before.database
    assert after.database_parent == before.database_parent
    assert after.data_root == before.data_root
    assert after.blob_root == before.blob_root
    assert after.projection_root == before.projection_root


@pytest.mark.unit
def test_p1_3_storage_layout_requires_all_four_explicit_absolute_paths(
    isolated_knowledge_layout,
) -> None:
    with pytest.raises(KnowledgeCoreDatabasePathError):
        MigrationStorageLayout(
            data_root=Path("relative"),
            database=isolated_knowledge_layout.core_db_path,
            blob_root=isolated_knowledge_layout.blobs_dir,
            projection_root=isolated_knowledge_layout.projections_dir,
        )
    with pytest.raises(KnowledgeCoreDatabasePathError):
        MigrationStorageLayout(
            data_root=Path(r"\\server\share\data"),
            database=isolated_knowledge_layout.core_db_path,
            blob_root=isolated_knowledge_layout.blobs_dir,
            projection_root=isolated_knowledge_layout.projections_dir,
        )
    with pytest.raises(KnowledgeCoreDatabasePathError):
        verify_migration_storage_layout(None, require_database=False)  # type: ignore[arg-type]


@pytest.mark.unit
def test_p1_3_removed_single_database_gate_fails_closed(
    isolated_knowledge_layout,
) -> None:
    with pytest.raises(KnowledgeCoreDatabasePathError):
        verify_migration_storage(
            isolated_knowledge_layout.core_db_path,
            require_database=False,
        )


@pytest.mark.unit
def test_p1_3_storage_layout_rejects_ineligible_path_before_creation(
    monkeypatch: pytest.MonkeyPatch,
    isolated_knowledge_layout,
) -> None:
    layout = _layout(isolated_knowledge_layout)

    def ineligible(candidate: Path) -> dict[str, object]:
        report = _eligible_report(str(candidate))
        if Path(candidate) == layout.blob_root:
            report["eligible"] = False
            report["reason_codes"] = ["filesystem_not_ntfs"]
        return report

    monkeypatch.setattr(windows_volume, "probe_path", ineligible)
    with pytest.raises(KnowledgeCoreDatabasePathError):
        verify_migration_storage_layout(layout, require_database=False)
    assert not layout.database.exists()


@pytest.mark.unit
@pytest.mark.parametrize("role", ["data", "database_parent", "blob", "projection"])
def test_p1_3_storage_layout_rejects_directory_volume_root(
    monkeypatch: pytest.MonkeyPatch,
    isolated_knowledge_layout,
    role: str,
) -> None:
    layout = _layout(isolated_knowledge_layout)
    targets = {
        "data": layout.data_root,
        "database_parent": layout.database.parent,
        "blob": layout.blob_root,
        "projection": layout.projection_root,
    }
    original = windows_volume.probe_path

    def root_alias(candidate: Path) -> dict[str, object]:
        if Path(candidate) == targets[role]:
            return _eligible_report("C:\\", root="C:\\")
        return original(candidate)

    monkeypatch.setattr(windows_volume, "probe_path", root_alias)
    with pytest.raises(KnowledgeCoreDatabasePathError):
        verify_migration_storage_layout(layout, require_database=False)


@pytest.mark.unit
@pytest.mark.parametrize(
    "case",
    ["database_outside_data", "database_in_blob", "database_in_projection", "roots_overlap"],
)
def test_p1_3_final_targets_enforce_layout_boundaries(
    monkeypatch: pytest.MonkeyPatch,
    isolated_knowledge_layout,
    case: str,
) -> None:
    layout = _layout(isolated_knowledge_layout)
    finals = {
        layout.data_root: r"D:\canonical\data",
        layout.database.parent: r"D:\canonical\data\core",
        layout.blob_root: r"D:\canonical\data\blobs",
        layout.projection_root: r"D:\canonical\data\projections",
    }
    database_final = r"D:\canonical\data\core\knowledge-core.db"
    if case == "database_outside_data":
        finals[layout.database.parent] = r"D:\other\core"
        database_final = r"D:\other\core\knowledge-core.db"
    elif case == "database_in_blob":
        finals[layout.database.parent] = r"D:\canonical\data\blobs\core"
        database_final = r"D:\canonical\data\blobs\core\knowledge-core.db"
    elif case == "database_in_projection":
        finals[layout.database.parent] = r"D:\canonical\data\projections\core"
        database_final = r"D:\canonical\data\projections\core\knowledge-core.db"
    elif case == "roots_overlap":
        finals[layout.projection_root] = r"D:\canonical\data\blobs\generated"

    def aliased(candidate: Path) -> dict[str, object]:
        path = Path(candidate)
        final = database_final if path == layout.database else finals[path]
        return _eligible_report(final, root="D:\\")

    monkeypatch.setattr(windows_volume, "probe_path", aliased)
    with pytest.raises(KnowledgeCoreDatabasePathError):
        verify_migration_storage_layout(layout, require_database=False)


@pytest.mark.unit
def test_p1_3_storage_layout_rejects_final_database_target_change(
    monkeypatch: pytest.MonkeyPatch,
    isolated_knowledge_layout,
) -> None:
    layout = _layout(isolated_knowledge_layout)
    original = windows_volume.probe_path
    expected = verify_migration_storage_layout(layout, require_database=False)
    raw = sqlite3.connect(layout.database)
    raw.close()

    def changed(candidate: Path) -> dict[str, object]:
        report = original(candidate)
        if Path(candidate) == layout.database:
            report["resolved_path"] = str(layout.database.with_name("different.db"))
        return report

    monkeypatch.setattr(windows_volume, "probe_path", changed)
    with pytest.raises(KnowledgeCoreDatabasePathError):
        verify_migration_storage_layout(
            layout,
            require_database=True,
            expected=expected,
        )


@pytest.mark.unit
def test_p1_3_storage_layout_rejects_existing_database_identity_replacement(
    isolated_knowledge_layout,
) -> None:
    layout = _layout(isolated_knowledge_layout)
    raw = sqlite3.connect(layout.database)
    raw.close()
    expected = verify_migration_storage_layout(layout, require_database=True)

    layout.database.unlink()
    replacement = sqlite3.connect(layout.database)
    replacement.close()

    with pytest.raises(KnowledgeCoreDatabasePathError):
        verify_migration_storage_layout(
            layout,
            require_database=True,
            expected=expected,
        )


@pytest.mark.unit
def test_p1_3_storage_layout_requires_complete_fixed_ntfs_facts(
    monkeypatch: pytest.MonkeyPatch,
    isolated_knowledge_layout,
) -> None:
    layout = _layout(isolated_knowledge_layout)
    malformed = _eligible_report(str(layout.data_root))
    malformed["volume"] = {"drive_type_code": 3, "filesystem": "NTFS"}
    monkeypatch.setattr(windows_volume, "probe_path", lambda _: malformed)
    with pytest.raises(KnowledgeCoreDatabasePathError):
        verify_migration_storage_layout(layout, require_database=False)

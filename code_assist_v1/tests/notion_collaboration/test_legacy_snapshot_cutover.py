from __future__ import annotations

import hashlib
import inspect
import json
from pathlib import Path
import sqlite3

import pytest

from code_assist_v1 import notion_workspace_collaboration_migration as migration
from code_assist_v1 import notion_workspace_store as workspace_store
from code_assist_v1.notion_collaboration import cutover as cutover_module
from code_assist_v1.notion_collaboration import legacy_snapshot as snapshot_module
from code_assist_v1.notion_collaboration.canonical_authority import (
    CanonicalAuthority,
    NotFoundOrDenied,
)
from code_assist_v1.notion_collaboration.capability_switches import (
    AGGREGATE_KEY,
    CANARY_SUBJECTS_KEY,
    CANARY_WORKSPACES_KEY,
    CAPABILITY_KEYS,
    GENERATION_KEY,
    REALM_KEY,
    ROLLOUT_KEY,
    RuntimeScope,
    load_capability_switches,
)
from code_assist_v1.notion_collaboration.cutover import (
    AdditiveForwardCompatibleWorkspaceStore,
    CutoverDisabled,
    DevelopmentCanonicalResolver,
)
from code_assist_v1.notion_collaboration.legacy_snapshot import (
    SnapshotContractError,
    import_sanitized_snapshot,
    verify_sanitized_snapshot,
    write_sanitized_snapshot,
)


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


def _migrated(tmp_path: Path) -> tuple[CanonicalAuthority, Path, Path, str, Path]:
    root = tmp_path / "import-target"
    catalog = Path(workspace_store.catalog_path_for_user_data_root(root))
    store = workspace_store.WorkspaceStore(catalog)
    store.bootstrap()
    created = store.create_workspace(
        "owner", name="Imported", operation_id="op-import-workspace-create-0001"
    )
    workspace_id = str(created["workspace"]["id"])
    backup = _backup(catalog, root / "backups" / "legacy-compatible-1317.db")
    plan = migration.build_plan(catalog, root)
    migration.apply_plan(
        catalog,
        root,
        exact_plan_checksum=plan["plan_checksum"],
        backup_confirmed=True,
        catalog_quiesced_confirmed=True,
        backup_path=backup,
    )
    return CanonicalAuthority.open(catalog, root), root, catalog, workspace_id, backup


def _snapshot(tmp_path: Path, workspace_id: str) -> Path:
    root = tmp_path / "sanitized-snapshot"
    write_sanitized_snapshot(
        root,
        allowed_parent=tmp_path,
        snapshot_id="snap_0123456789abcdef",
        workspace_id=workspace_id,
        owner_subject_id="owner",
        pages=[
            {
                "id": "page-imported",
                "owner_subject_id": "owner",
                "title": "Imported page",
                "revision": 3,
                "created_at": 10,
                "updated_at": 20,
                "blocks": [
                    {
                        "id": "block-imported",
                        "type": "text",
                        "content": "한글 snapshot",
                        "props": {"bold": True},
                    }
                ],
                "comments": [
                    {
                        "id": "comment-imported",
                        "author_subject_id": "owner",
                        "content": "reviewed",
                        "created_at": 12,
                        "updated_at": 12,
                    }
                ],
                "shares": [{"subject_id": "guest", "role": "view"}],
            }
        ],
    )
    return root


def _enabled(workspace_id: str):
    generation = "dev-generation-a0b-0001"
    load = load_capability_switches(
        {
            AGGREGATE_KEY: "true",
            REALM_KEY: "development",
            ROLLOUT_KEY: "canary",
            GENERATION_KEY: generation,
            CANARY_SUBJECTS_KEY: json.dumps(["guest", "owner"]),
            CANARY_WORKSPACES_KEY: json.dumps([workspace_id]),
            CAPABILITY_KEYS["canonical_authority"]: "true",
        }
    )
    assert load.enabled
    return load, RuntimeScope(
        realm="development",
        deployment_generation_id=generation,
        schema_compatible=True,
    )


@pytest.mark.migration
@pytest.mark.integration
def test_a0b_p1_deterministic_snapshot_import_replays_and_matches_exact_content_hash(
    tmp_path: Path,
) -> None:
    authority, _, _, workspace_id, _ = _migrated(tmp_path)
    snapshot = _snapshot(tmp_path, workspace_id)
    before = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in snapshot.iterdir()
    }
    imported = import_sanitized_snapshot(
        authority,
        snapshot,
        allowed_parent=tmp_path,
        actor_subject_id="owner",
        operation_id="op-import-snapshot-0001",
    )
    replay = import_sanitized_snapshot(
        authority,
        snapshot,
        allowed_parent=tmp_path,
        actor_subject_id="owner",
        operation_id="op-import-snapshot-0001",
    )
    assert replay == imported
    assert imported["source_content_set_sha256"] == imported["canonical_content_set_sha256"]
    assert imported["legacy_source_write_count"] == 0
    assert imported["request_time_directory_scan_count"] == 0
    assert before == {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in snapshot.iterdir()
    }
    page = authority.read_document("owner", "page-imported")
    assert page["revision"] == 3
    assert page["blocks"][0]["content"] == "한글 snapshot"
    assert page["comments"][0]["content"] == "reviewed"
    assert authority.read_document("guest", "page-imported")["role"] == "view"
    with pytest.raises(NotFoundOrDenied):
        authority.read_document("outsider", "page-imported")
    assert authority.counts()["workspace_collaboration_imports"] == 1


@pytest.mark.integration
def test_a0b_p1_default_off_exact_canary_projection_and_post_write_rollback_contract(
    tmp_path: Path,
) -> None:
    authority, root, catalog, workspace_id, backup = _migrated(tmp_path)
    snapshot = _snapshot(tmp_path, workspace_id)
    import_sanitized_snapshot(
        authority,
        snapshot,
        allowed_parent=tmp_path,
        actor_subject_id="owner",
        operation_id="op-import-cutover-0001",
    )
    with pytest.raises(CutoverDisabled):
        DevelopmentCanonicalResolver.open(
            catalog,
            root,
            switch_load=load_capability_switches({}),
            runtime_scope=RuntimeScope(
                realm="development",
                deployment_generation_id="dev-generation-a0b-0001",
                schema_compatible=True,
            ),
        )

    load, runtime = _enabled(workspace_id)
    resolver = DevelopmentCanonicalResolver.open(
        catalog, root, switch_load=load, runtime_scope=runtime
    )
    with pytest.raises(CutoverDisabled):
        resolver.read_document("outsider", workspace_id, "page-imported")
    page = resolver.read_document("owner", workspace_id, "page-imported")
    assert resolver.rollback_state(workspace_id)["rollback_mode"] == (
        "disable_candidate_and_return_to_unchanged_legacy"
    )
    projection_root = tmp_path / "compatibility-projection"
    projection = resolver.compatibility_projection(
        "owner",
        workspace_id,
        "page-imported",
        projection_root,
        allowed_parent=tmp_path,
    )
    assert projection["legacy_write_count"] == 0
    projection_page = json.loads((projection_root / "page.json").read_text(encoding="utf-8"))
    assert projection_page["canonical_content_sha256"] == page["content_sha256"]
    assert projection_page["legacy"]["page"]["revision"] == 3

    saved = resolver.save_document(
        "owner",
        workspace_id,
        "page-imported",
        title="Cut over",
        blocks=[{"id": "block-imported", "type": "text", "content": "candidate write"}],
        expected_revision=3,
        expected_workspace_epoch=1,
        expected_document_epoch=1,
        operation_id="op-cutover-save-0001",
    )
    assert saved["revision"] == 4
    state = resolver.rollback_state(workspace_id)
    assert state["runtime_write_count"] == 1
    assert state["rollback_mode"] == (
        "roll_forward_or_restore_candidate_never_overlay_from_legacy"
    )
    assert state["schema_downgrade_required"] is False
    con = sqlite3.connect(f"{backup.resolve().as_uri()}?mode=ro", uri=True)
    try:
        assert con.execute("SELECT version FROM workspace_schema_migrations").fetchone()[0] == 2026081317
        assert con.execute(
            "SELECT 1 FROM sqlite_master WHERE name='workspace_collaboration_documents'"
        ).fetchone() is None
    finally:
        con.close()
    compatible = AdditiveForwardCompatibleWorkspaceStore(catalog)
    assert compatible.list_workspaces("owner")[0]["id"] == workspace_id


def test_a0b_p1_snapshot_tamper_extra_file_and_discovery_apis_fail_closed(
    tmp_path: Path,
) -> None:
    authority, _, _, workspace_id, _ = _migrated(tmp_path)
    del authority
    snapshot = _snapshot(tmp_path, workspace_id)
    (snapshot / "unexpected.txt").write_text("no", encoding="utf-8")
    with pytest.raises(SnapshotContractError, match="file set"):
        verify_sanitized_snapshot(snapshot, allowed_parent=tmp_path)

    source = inspect.getsource(snapshot_module.import_sanitized_snapshot)
    resolver_source = inspect.getsource(cutover_module.DevelopmentCanonicalResolver)
    for forbidden in ("os.listdir", ".glob(", ".rglob(", "notion.db", "USER_DATA_DIR"):
        assert forbidden not in source
        assert forbidden not in resolver_source
    assert "source_root" not in inspect.signature(write_sanitized_snapshot).parameters
    assert "database_path" not in inspect.signature(write_sanitized_snapshot).parameters

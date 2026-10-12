from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from code_assist_v1 import notion_workspace_collaboration_migration as canonical_migration
from code_assist_v1 import notion_workspace_iam_migration as iam_migration
from code_assist_v1 import notion_workspace_realtime_transport_migration as transport_migration
from code_assist_v1 import notion_workspace_store as workspace_store
from code_assist_v1.notion_collaboration.canonical_authority import (
    InjectedFailure,
    NotFoundOrDenied,
    OperationConflict,
    queue_frame,
)
from code_assist_v1.notion_collaboration.iam_authority import (
    CollaborativeIAMAuthority,
    InMemoryIdentityDirectory,
)
from code_assist_v1.tools.verify_internal_collaborative_iam_authority import (
    DEFAULT_CONTRACT as IAM_CONTRACT,
    verify as verify_iam_contract,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
ACTIVE_SUBJECTS = (
    "owner",
    "member",
    "restricted",
    "team-member",
    "page-guest",
    "new-member",
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


def _apply(migration, catalog: Path, root: Path, name: str) -> None:
    backup = _backup(catalog, root / "backups" / name)
    plan = migration.build_plan(catalog, root)
    migration.apply_plan(
        catalog,
        root,
        exact_plan_checksum=plan["plan_checksum"],
        backup_confirmed=True,
        catalog_quiesced_confirmed=True,
        backup_path=backup,
    )


def _environment(
    tmp_path: Path,
) -> tuple[CollaborativeIAMAuthority, InMemoryIdentityDirectory, Path, Path, str, str, str]:
    root = tmp_path / "i0-p1-authority"
    catalog = Path(workspace_store.catalog_path_for_user_data_root(root))
    store = workspace_store.WorkspaceStore(catalog)
    store.bootstrap()
    created = store.create_workspace(
        "owner", name="I0 workspace", operation_id="op-i0-workspace-create-0001"
    )
    workspace_id = str(created["workspace"]["id"])
    version = int(created["workspace"]["version"])
    for index, (subject, role) in enumerate(
        (("member", "member"), ("restricted", "restricted_member"), ("team-member", "member")),
        start=1,
    ):
        result = store.set_member(
            "owner",
            workspace_id,
            subject,
            role=role,
            base_version=version,
            operation_id=f"op-i0-member-set-{index:04d}",
        )
        version = int(result["workspace"]["version"])
    group = store.create_group(
        "owner",
        workspace_id,
        name="Editors",
        base_version=version,
        operation_id="op-i0-group-create-0001",
    )
    version = int(group["workspace"]["version"])
    group_id = str(group["group"]["id"])
    group_member = store.set_group_member(
        "owner",
        workspace_id,
        group_id,
        "restricted",
        base_version=version,
        operation_id="op-i0-group-member-set-0001",
    )
    version = int(group_member["workspace"]["version"])
    teamspace = store.create_teamspace(
        "owner",
        workspace_id,
        name="Teamspace",
        visibility="closed",
        base_version=version,
        operation_id="op-i0-teamspace-create-0001",
    )
    version = int(teamspace["workspace"]["version"])
    teamspace_id = str(teamspace["teamspace"]["id"])
    team_member = store.set_teamspace_member(
        "owner",
        workspace_id,
        teamspace_id,
        "team-member",
        role="member",
        base_version=version,
        operation_id="op-i0-teamspace-member-set-0001",
    )
    version = int(team_member["workspace"]["version"])

    _apply(canonical_migration, catalog, root, "before-2401.db")
    _apply(transport_migration, catalog, root, "before-2402.db")
    _apply(iam_migration, catalog, root, "before-2403.db")
    identities = InMemoryIdentityDirectory(ACTIVE_SUBJECTS)
    authority = CollaborativeIAMAuthority.open(catalog, root, identities)
    con = sqlite3.connect(catalog)
    try:
        workspace_epoch = int(
            con.execute("SELECT security_epoch FROM workspaces WHERE id=?", (workspace_id,)).fetchone()[0]
        )
    finally:
        con.close()
    authority.create_document(
        "owner",
        workspace_id,
        "page-root",
        title="Root",
        blocks=[{"id": "block-root", "type": "text", "content": "root"}],
        expected_workspace_epoch=workspace_epoch,
        operation_id="op-i0-page-root-create-0001",
    )
    authority.create_document(
        "owner",
        workspace_id,
        "page-child",
        title="Child",
        blocks=[{"id": "block-child", "type": "text", "content": "child"}],
        parent_document_id="page-root",
        expected_workspace_epoch=workspace_epoch,
        operation_id="op-i0-page-child-create-0001",
    )
    return authority, identities, root, catalog, workspace_id, group_id, teamspace_id


def _epochs(authority: CollaborativeIAMAuthority, document_id: str) -> tuple[int, int]:
    page = authority.read_document("owner", document_id)
    return int(page["workspace_epoch"]), int(page["document_epoch"])


@pytest.mark.unit
def test_i0_p1_checked_in_contract_is_fail_closed(tmp_path: Path) -> None:
    report = verify_iam_contract()
    assert report["status"] == "pass", report
    tampered = json.loads(IAM_CONTRACT.read_text(encoding="utf-8"))
    tampered["role_resolver"]["roles_low_to_high"] = ["view", "full_access"]
    path = tmp_path / "tampered-iam-contract.json"
    path.write_text(json.dumps(tampered), encoding="utf-8")
    report = verify_iam_contract(path)
    assert report["status"] == "fail"
    assert "ROLE_RESOLVER_DRIFT" in {item["code"] for item in report["errors"]}


@pytest.mark.migration
def test_i0_p1_2403_migration_is_forward_only_checksummed_and_guarded(tmp_path: Path) -> None:
    authority, _, root, catalog, _, _, _ = _environment(tmp_path)
    assert authority.integrity() == {
        "contract": "code_assist.notion.collaborative_iam_authority.v1",
        "quick_check": "ok",
        "foreign_key_error_count": 0,
        "schema_version": 2026082403,
        "schema_marker_count": 1,
        "identity_directory": {"generation": 1, "active_human_count": 6},
        "request_time_directory_scan_count": 0,
        "legacy_source_database_open_count": 0,
        "dual_write_count": 0,
    }
    plan = iam_migration.build_plan(catalog, root)
    assert plan["state"] == "already_current"
    assert iam_migration.SOURCE_VERSION == 2026082402
    assert iam_migration.TARGET_VERSION == 2026082403
    assert iam_migration.SOURCE_SQL == transport_migration.TARGET_SQL
    assert iam_migration.TARGET_SQL.endswith(iam_migration.IAM_SCHEMA_SQL)


@pytest.mark.migration
def test_i0_p1_2403_wrong_checksum_and_fault_leave_2402_unchanged(tmp_path: Path) -> None:
    root = tmp_path / "i0-migration-guard"
    catalog = Path(workspace_store.catalog_path_for_user_data_root(root))
    workspace_store.WorkspaceStore(catalog).bootstrap()
    _apply(canonical_migration, catalog, root, "guard-before-2401.db")
    _apply(transport_migration, catalog, root, "guard-before-2402.db")
    backup = _backup(catalog, root / "backups" / "guard-before-2403.db")
    plan = iam_migration.build_plan(catalog, root)
    before = catalog.read_bytes()
    with pytest.raises(iam_migration.MigrationRefused, match="checksum"):
        iam_migration.apply_plan(
            catalog,
            root,
            exact_plan_checksum="sha256:" + "0" * 64,
            backup_confirmed=True,
            catalog_quiesced_confirmed=True,
            backup_path=backup,
        )
    assert catalog.read_bytes() == before

    def fail(point: str) -> None:
        if point == "after_schema":
            raise RuntimeError("i0-injected-before-publish")

    with pytest.raises(RuntimeError, match="i0-injected-before-publish"):
        iam_migration.apply_plan(
            catalog,
            root,
            exact_plan_checksum=plan["plan_checksum"],
            backup_confirmed=True,
            catalog_quiesced_confirmed=True,
            backup_path=backup,
            fault_hook=fail,
        )
    assert catalog.read_bytes() == before
    con = sqlite3.connect(catalog)
    try:
        assert con.execute("SELECT version FROM workspace_schema_migrations").fetchone()[0] == 2026082402
    finally:
        con.close()


@pytest.mark.integration
def test_i0_p1_resolver_combines_direct_inherited_group_and_teamspace_grants(
    tmp_path: Path,
) -> None:
    authority, _, _, _, _, group_id, teamspace_id = _environment(tmp_path)
    workspace_epoch, root_epoch = _epochs(authority, "page-root")
    subject_grant = authority.set_page_grant(
        "owner",
        "page-root",
        "subject",
        "page-guest",
        role="view",
        inherit_to_descendants=True,
        expected_workspace_epoch=workspace_epoch,
        expected_document_epoch=root_epoch,
        operation_id="op-i0-subject-grant-0001",
    )
    assert subject_grant["affected_document_ids"] == ["page-root", "page-child"]
    guest = authority.resolve_access("page-guest", "page-child")
    assert guest.role == "view"
    assert guest.capabilities == ("read",)
    assert guest.sources[0]["kind"] == "subject_grant"
    assert guest.sources[0]["inherited"] is True

    workspace_epoch, root_epoch = _epochs(authority, "page-root")
    authority.set_page_grant(
        "owner",
        "page-root",
        "group",
        group_id,
        role="edit",
        inherit_to_descendants=True,
        expected_workspace_epoch=workspace_epoch,
        expected_document_epoch=root_epoch,
        operation_id="op-i0-group-grant-0001",
    )
    group_access = authority.resolve_access("restricted", "page-child")
    assert group_access.role == "edit"
    assert "edit_schema" in group_access.capabilities
    assert {item["kind"] for item in group_access.sources} == {"group_grant"}

    workspace_epoch, root_epoch = _epochs(authority, "page-root")
    authority.set_page_grant(
        "owner",
        "page-root",
        "teamspace",
        teamspace_id,
        role="edit_content",
        inherit_to_descendants=True,
        expected_workspace_epoch=workspace_epoch,
        expected_document_epoch=root_epoch,
        operation_id="op-i0-teamspace-grant-0001",
    )
    team_access = authority.resolve_access("team-member", "page-child")
    assert team_access.role == "edit_content"
    assert team_access.capabilities == ("read", "comment", "edit_content")
    assert {item["kind"] for item in team_access.sources} == {"teamspace_grant"}

    with pytest.raises(NotFoundOrDenied, match="not found or permission denied"):
        authority.resolve_access("member", "page-child")
    with pytest.raises(NotFoundOrDenied, match="not found or permission denied"):
        authority.resolve_access("member", "page-absent")


@pytest.mark.integration
def test_i0_p1_workspace_invite_role_change_revoke_rejoin_and_last_owner_rules(
    tmp_path: Path,
) -> None:
    authority, identities, _, _, workspace_id, _, _ = _environment(tmp_path)
    workspace_epoch, _ = _epochs(authority, "page-root")
    with pytest.raises(ValueError, match="retain at least one owner"):
        authority.set_workspace_member(
            "owner",
            workspace_id,
            "owner",
            role="member",
            expected_workspace_epoch=workspace_epoch,
            operation_id="op-i0-last-owner-demote-0001",
        )

    invited = authority.set_workspace_member(
        "owner",
        workspace_id,
        "new-member",
        role="owner",
        expected_workspace_epoch=workspace_epoch,
        operation_id="op-i0-new-owner-set-0001",
    )
    replay = authority.set_workspace_member(
        "owner",
        workspace_id,
        "new-member",
        role="owner",
        expected_workspace_epoch=workspace_epoch,
        operation_id="op-i0-new-owner-set-0001",
    )
    assert replay == invited
    with pytest.raises(OperationConflict):
        authority.set_workspace_member(
            "owner",
            workspace_id,
            "new-member",
            role="member",
            expected_workspace_epoch=workspace_epoch,
            operation_id="op-i0-new-owner-set-0001",
        )

    demoted = authority.set_workspace_member(
        "owner",
        workspace_id,
        "owner",
        role="member",
        expected_workspace_epoch=invited["workspace_epoch"],
        operation_id="op-i0-owner-self-demote-0001",
    )
    assert demoted["role"] == "member"
    assert authority.set_workspace_member(
        "owner",
        workspace_id,
        "owner",
        role="member",
        expected_workspace_epoch=invited["workspace_epoch"],
        operation_id="op-i0-owner-self-demote-0001",
    ) == demoted
    revoked = authority.revoke_workspace_member(
        "new-member",
        workspace_id,
        "member",
        expected_workspace_epoch=demoted["workspace_epoch"],
        operation_id="op-i0-member-revoke-0001",
    )
    assert revoked["revoked_subject_id"] == "member"
    rejoined = authority.set_workspace_member(
        "new-member",
        workspace_id,
        "member",
        role="member",
        expected_workspace_epoch=revoked["workspace_epoch"],
        operation_id="op-i0-member-rejoin-0001",
    )
    assert rejoined["membership_epoch"] >= 3
    for operation_id in (
        "op-i0-new-owner-set-0001",
        "op-i0-owner-self-demote-0001",
        "op-i0-member-revoke-0001",
        "op-i0-member-rejoin-0001",
    ):
        assert len(
            authority.rows(
                "workspace_collaboration_events", operation_id=operation_id
            )
        ) == 1
        assert len(
            authority.rows(
                "workspace_collaboration_audit_events", operation_id=operation_id
            )
        ) == 1
        assert len(
            authority.rows(
                "workspace_collaboration_outbox", operation_id=operation_id
            )
        ) == 1

    identities.replace(tuple(item for item in ACTIVE_SUBJECTS if item != "member"))
    before = authority.counts()["workspace_collaboration_operations"]
    with pytest.raises(NotFoundOrDenied, match="not found or permission denied"):
        authority.set_workspace_member(
            "new-member",
            workspace_id,
            "member",
            role="member",
            expected_workspace_epoch=rejoined["workspace_epoch"],
            operation_id="op-i0-disabled-target-set-0001",
        )
    assert authority.counts()["workspace_collaboration_operations"] == before


@pytest.mark.integration
@pytest.mark.fault
def test_i0_p1_revoke_fences_old_frames_and_faults_roll_back_atomically(tmp_path: Path) -> None:
    authority, identities, _, catalog, _, _, _ = _environment(tmp_path)
    workspace_epoch, root_epoch = _epochs(authority, "page-root")
    grant = authority.set_page_grant(
        "owner",
        "page-root",
        "subject",
        "page-guest",
        role="comment",
        inherit_to_descendants=True,
        expected_workspace_epoch=workspace_epoch,
        expected_document_epoch=root_epoch,
        operation_id="op-i0-revoke-seed-0001",
    )
    frame = queue_frame(authority, "page-guest", "page-child", {"secret": "not-delivered"})
    assert authority.authorize_frame_send(frame).delivered is True
    revoked = authority.revoke_page_grant(
        "owner",
        "page-root",
        "subject",
        "page-guest",
        expected_workspace_epoch=grant["workspace_epoch"],
        expected_document_epoch=grant["document_epoch"],
        operation_id="op-i0-page-grant-revoke-0001",
    )
    assert revoked["state"] == "revoked"
    assert authority.authorize_frame_send(frame).payload is None
    with pytest.raises(NotFoundOrDenied):
        authority.resolve_access("page-guest", "page-child")

    workspace_epoch, root_epoch = _epochs(authority, "page-root")
    con = sqlite3.connect(catalog)
    try:
        before = tuple(
            con.execute(
                "SELECT "
                "(SELECT COUNT(*) FROM workspace_collaboration_access_grants),"
                "(SELECT COUNT(*) FROM workspace_collaboration_events),"
                "(SELECT COUNT(*) FROM workspace_collaboration_audit_events),"
                "(SELECT COUNT(*) FROM workspace_collaboration_outbox),"
                "(SELECT COUNT(*) FROM workspace_collaboration_operations)"
            ).fetchone()
        )
    finally:
        con.close()
    with pytest.raises(InjectedFailure, match="after_event"):
        authority.set_page_grant(
            "owner",
            "page-root",
            "subject",
            "page-guest",
            role="view",
            inherit_to_descendants=False,
            expected_workspace_epoch=workspace_epoch,
            expected_document_epoch=root_epoch,
            operation_id="op-i0-grant-fault-0001",
            fault="after_event",
        )
    con = sqlite3.connect(catalog)
    try:
        after = tuple(
            con.execute(
                "SELECT "
                "(SELECT COUNT(*) FROM workspace_collaboration_access_grants),"
                "(SELECT COUNT(*) FROM workspace_collaboration_events),"
                "(SELECT COUNT(*) FROM workspace_collaboration_audit_events),"
                "(SELECT COUNT(*) FROM workspace_collaboration_outbox),"
                "(SELECT COUNT(*) FROM workspace_collaboration_operations)"
            ).fetchone()
        )
    finally:
        con.close()
    assert after == before

    identities.replace(tuple(item for item in ACTIVE_SUBJECTS if item != "page-guest"))
    assert authority.authorize_frame_send(frame).payload is None


@pytest.mark.integration
def test_i0_p1_restart_process_resolves_same_non_oracle_authority(tmp_path: Path) -> None:
    authority, _, root, catalog, _, _, _ = _environment(tmp_path)
    workspace_epoch, root_epoch = _epochs(authority, "page-root")
    authority.set_page_grant(
        "owner",
        "page-root",
        "subject",
        "page-guest",
        role="view",
        inherit_to_descendants=True,
        expected_workspace_epoch=workspace_epoch,
        expected_document_epoch=root_epoch,
        operation_id="op-i0-process-grant-0001",
    )
    command = {
        "catalog_path": str(catalog),
        "user_data_root": str(root),
        "active_subject_ids": list(ACTIVE_SUBJECTS),
        "command": "resolve_access",
        "arguments": {"subject_id": "page-guest", "document_id": "page-child"},
    }
    script = (
        "import json,sys;"
        "from code_assist_v1.notion_collaboration.iam_authority import execute_iam_command;"
        "print(json.dumps(execute_iam_command(json.load(sys.stdin)),sort_keys=True))"
    )
    child = subprocess.Popen(
        [sys.executable, "-E", "-c", script],
        cwd=REPOSITORY_ROOT,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    stdout, stderr = child.communicate(json.dumps(command), timeout=20)
    assert (child.returncode, stderr) == (0, "")
    result = json.loads(stdout)
    assert result["role"] == "view"
    assert result["document_id"] == "page-child"
    assert result["sources"][0]["inherited"] is True

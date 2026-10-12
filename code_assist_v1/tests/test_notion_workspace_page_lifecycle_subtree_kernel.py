from __future__ import annotations

from dataclasses import replace

import pytest

from code_assist_v1.notion_workspace_store import (
    WorkspacePageTreeSourceFenceConflict,
)
from code_assist_v1.tests.test_notion_workspace_page_lifecycle_kernel import (
    _materialized,
)


@pytest.mark.parametrize(
    ("action", "canonical_action"),
    (("duplicate", "duplicate"), ("archive", "trash")),
)
def test_subtree_kernel_prepares_exact_canonical_action_and_replays(
    tmp_path, monkeypatch, action: str, canonical_action: str
) -> None:
    prepared, request = _materialized(tmp_path, monkeypatch)
    request = replace(request, action=action)
    operation_id = f"{action}-subtree-kernel-1"

    created = prepared["store"].prepare_page_lifecycle_command(
        "reviewer", request, operation_id=operation_id
    )
    assert created["action"] == action
    assert created["status"] == "prepared"
    assert prepared["store"].get_page_lifecycle_command_replay(
        "reviewer", request, operation_id=operation_id
    ) == created

    con = prepared["store"]._read_connection()
    try:
        row = con.execute(
            "SELECT canonical_lifecycle_action "
            "FROM workspace_page_lifecycle_commands WHERE id=?",
            (created["command_id"],),
        ).fetchone()
        assert row is not None
        assert row["canonical_lifecycle_action"] == canonical_action
    finally:
        con.close()


@pytest.mark.parametrize("action", ("duplicate", "archive"))
def test_subtree_kernel_rejects_structural_targets_for_non_restore_actions(
    tmp_path, monkeypatch, action: str
) -> None:
    prepared, request = _materialized(tmp_path, monkeypatch)
    request = replace(
        request,
        action=action,
        parent_source_owner_subject_id="owner",
        parent_source_page_id=str(prepared["root"]),
    )

    with pytest.raises(ValueError, match="does not accept structural targets"):
        prepared["store"].prepare_page_lifecycle_command(
            "reviewer", request, operation_id=f"{action}-bad-placement-1"
        )


def test_restore_prepare_requires_exact_archived_source_and_trashed_tree(
    tmp_path, monkeypatch
) -> None:
    prepared, request = _materialized(tmp_path, monkeypatch)
    request = replace(request, action="restore")

    with pytest.raises(
        WorkspacePageTreeSourceFenceConflict,
        match="restore generation changed",
    ):
        prepared["store"].prepare_page_lifecycle_command(
            "reviewer", request, operation_id="restore-active-source-1"
        )


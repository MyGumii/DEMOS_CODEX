from __future__ import annotations

import json
from pathlib import Path

import pytest

from code_assist_v1.notion_collaboration.canonical_authority_prototype import (
    DisposableAuthority,
    NotFoundOrDenied,
    OperationConflict,
    PrototypeBoundaryError,
    RevisionConflict,
    initialize_disposable_authority,
)


def _authority(tmp_path: Path) -> DisposableAuthority:
    return initialize_disposable_authority(tmp_path / "a0a-p2-realm")


def _seed(authority: DisposableAuthority) -> tuple[dict, dict]:
    workspace = authority.create_workspace(
        "owner", "workspace-a", operation_id="op-workspace-create-0001"
    )
    page = authority.create_page(
        "owner",
        "workspace-a",
        "page-a",
        "Authority prototype",
        {"blocks": [{"id": "block-a", "text": "initial"}]},
        expected_workspace_epoch=workspace["workspace_epoch"],
        operation_id="op-page-create-0001",
    )
    return workspace, page


@pytest.mark.unit
@pytest.mark.unit
@pytest.mark.migration
def test_a0a_p2_is_marker_bound_unversioned_and_temp_only(tmp_path: Path) -> None:
    authority = _authority(tmp_path)
    assert authority.database_path.parent == tmp_path / "a0a-p2-realm"
    assert authority.integrity() == {
        "quick_check": "ok",
        "foreign_key_error_count": 0,
        "contract": "code_assist.notion.a0a_p2.disposable_authority.v1",
        "disposable": True,
        "schema_mode": "unversioned",
        "production_schema_version": "null",
        "migration_table_count": 0,
    }

    with pytest.raises(PrototypeBoundaryError):
        DisposableAuthority.open(tmp_path)
    with pytest.raises(PrototypeBoundaryError):
        initialize_disposable_authority(Path.cwd() / "forbidden-a0a-p2-root")

    marker = authority.root / ".a0a-p2-disposable-authority.json"
    value = json.loads(marker.read_text(encoding="utf-8"))
    value["production_schema_version"] = 2026081318
    marker.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(PrototypeBoundaryError):
        DisposableAuthority.open(authority.root)


@pytest.mark.integration
def test_a0a_p2_one_authority_owns_content_revision_event_audit_and_outbox(tmp_path: Path) -> None:
    authority = _authority(tmp_path)
    _, created = _seed(authority)
    assert created["revision"] == 1
    assert created["page_sequence"] == 1
    assert created["workspace_epoch"] == 1
    assert created["page_epoch"] == 1
    assert authority.counts() == {
        "workspaces": 1,
        "workspace_principals": 1,
        "pages": 1,
        "page_grants": 0,
        "operation_dedup": 2,
        "durable_events": 2,
        "audit_events": 2,
        "outbox_events": 2,
    }

    page = authority.read_page(
        "owner",
        "page-a",
        expected_workspace_epoch=1,
        expected_page_epoch=1,
    )
    assert page["content"] == {"blocks": [{"id": "block-a", "text": "initial"}]}
    assert page["revision"] == 1
    assert page["page_sequence"] == 1
    assert authority.integrity()["foreign_key_error_count"] == 0


@pytest.mark.integration
def test_a0a_p2_save_uses_revision_cas_and_durable_operation_replay(tmp_path: Path) -> None:
    authority = _authority(tmp_path)
    _seed(authority)
    result = authority.save_page(
        "owner",
        "page-a",
        {"blocks": [{"id": "block-a", "text": "saved"}]},
        expected_revision=1,
        expected_workspace_epoch=1,
        expected_page_epoch=1,
        operation_id="op-page-save-0001",
    )
    replay = authority.save_page(
        "owner",
        "page-a",
        {"blocks": [{"id": "block-a", "text": "saved"}]},
        expected_revision=1,
        expected_workspace_epoch=1,
        expected_page_epoch=1,
        operation_id="op-page-save-0001",
    )
    assert replay == result
    assert authority.counts()["operation_dedup"] == 3
    assert authority.counts()["durable_events"] == 3

    with pytest.raises(OperationConflict):
        authority.save_page(
            "owner",
            "page-a",
            {"blocks": [{"id": "block-a", "text": "different hash"}]},
            expected_revision=1,
            expected_workspace_epoch=1,
            expected_page_epoch=1,
            operation_id="op-page-save-0001",
        )
    with pytest.raises(RevisionConflict) as stale:
        authority.save_page(
            "owner",
            "page-a",
            {"blocks": []},
            expected_revision=1,
            expected_workspace_epoch=1,
            expected_page_epoch=1,
            operation_id="op-page-save-stale-0002",
        )
    assert stale.value.current_revision == 2


@pytest.mark.integration
def test_a0a_p2_outsider_absence_and_denial_share_one_oracle(tmp_path: Path) -> None:
    authority = _authority(tmp_path)
    _seed(authority)
    messages: list[str] = []
    for page_id in ("page-a", "page-absent"):
        with pytest.raises(NotFoundOrDenied) as denied:
            authority.read_page(
                "outsider",
                page_id,
                expected_workspace_epoch=1,
                expected_page_epoch=1,
            )
        messages.append(str(denied.value))
    assert messages == ["not found or permission denied"] * 2

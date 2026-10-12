from __future__ import annotations

from pathlib import Path

import pytest

from code_assist_v1.notion_collaboration.canonical_authority_prototype import (
    AuthorityUnavailable,
    DisposableAuthority,
    EpochConflict,
    NotFoundOrDenied,
    initialize_disposable_authority,
    queue_frame,
)


def _shared_authority(tmp_path: Path) -> DisposableAuthority:
    authority = initialize_disposable_authority(tmp_path / "iam-realm")
    authority.create_workspace(
        "owner", "workspace-a", operation_id="op-workspace-create-iam-0001"
    )
    member = authority.add_member(
        "owner",
        "workspace-a",
        "member",
        "edit",
        expected_workspace_epoch=1,
        operation_id="op-member-add-iam-0001",
    )
    assert member["workspace_epoch"] == 2
    authority.create_page(
        "owner",
        "workspace-a",
        "page-a",
        "Shared",
        {"blocks": [{"id": "block-a", "text": "protected"}]},
        expected_workspace_epoch=2,
        operation_id="op-page-create-iam-0001",
    )
    granted = authority.grant_page_access(
        "owner",
        "page-a",
        "guest",
        "view",
        expected_workspace_epoch=2,
        expected_page_epoch=1,
        operation_id="op-page-grant-iam-0001",
    )
    assert granted["page_epoch"] == 2
    return authority


@pytest.mark.integration
def test_a0a_p2_workspace_and_page_epochs_bind_every_principal_read(tmp_path: Path) -> None:
    authority = _shared_authority(tmp_path)
    owner = authority.binding("owner", "page-a")
    member = authority.binding("member", "page-a")
    guest = authority.binding("guest", "page-a")
    assert (owner.workspace_epoch, owner.page_epoch, owner.role) == (2, 2, "owner")
    assert (member.workspace_epoch, member.page_epoch, member.role) == (2, 2, "edit")
    assert (guest.workspace_epoch, guest.page_epoch, guest.role) == (2, 2, "view")

    with pytest.raises(EpochConflict):
        authority.read_page(
            "member",
            "page-a",
            expected_workspace_epoch=1,
            expected_page_epoch=2,
        )
    with pytest.raises(EpochConflict):
        authority.read_page(
            "guest",
            "page-a",
            expected_workspace_epoch=2,
            expected_page_epoch=1,
        )


@pytest.mark.integration
def test_a0a_p2_revoke_commits_without_gateway_and_old_frame_delivers_zero_payload(tmp_path: Path) -> None:
    authority = _shared_authority(tmp_path)
    queued = queue_frame(
        authority,
        "guest",
        "page-a",
        {"type": "page.saved", "protected": "must-not-leak"},
    )
    assert authority.authorize_frame_send(queued).delivered is True

    revoked = authority.revoke_subject(
        "owner",
        "workspace-a",
        "guest",
        expected_workspace_epoch=2,
        operation_id="op-guest-revoke-iam-0001",
    )
    assert revoked["state"] == "revoked"
    assert revoked["http_status"] == 200
    assert revoked["gateway_notification"] == "durable_outbox_pending"
    assert revoked["workspace_epoch"] == 3
    assert revoked["affected_page_epochs"] == {"page-a": 3}

    delivery = authority.authorize_frame_send(queued)
    assert delivery.descriptor() == {
        "delivered": False,
        "reason": "denied_or_authority_unavailable",
        "payload": None,
        "page_sequence": None,
    }
    with pytest.raises(NotFoundOrDenied):
        authority.binding("guest", "page-a")

    replay = authority.revoke_subject(
        "owner",
        "workspace-a",
        "guest",
        expected_workspace_epoch=2,
        operation_id="op-guest-revoke-iam-0001",
    )
    assert replay == revoked
    assert len(authority.outbox_rows(operation_id="op-guest-revoke-iam-0001")) == 1


@pytest.mark.integration
def test_a0a_p2_member_revoke_fences_stale_write_replay_search_and_subscription_binding(
    tmp_path: Path,
) -> None:
    authority = _shared_authority(tmp_path)
    member_frame = queue_frame(authority, "member", "page-a", {"protected": "event"})
    revoked = authority.revoke_subject(
        "owner",
        "workspace-a",
        "member",
        expected_workspace_epoch=2,
        operation_id="op-member-revoke-iam-0002",
    )
    assert revoked["workspace_epoch"] == 3
    assert authority.authorize_frame_send(member_frame).payload is None

    with pytest.raises(NotFoundOrDenied):
        authority.save_page(
            "member",
            "page-a",
            {"blocks": [{"text": "stale edit"}]},
            expected_revision=1,
            expected_workspace_epoch=2,
            expected_page_epoch=2,
            operation_id="op-member-stale-save-0001",
        )
    with pytest.raises(NotFoundOrDenied):
        authority.read_page(
            "member",
            "page-a",
            expected_workspace_epoch=3,
            expected_page_epoch=2,
        )


@pytest.mark.fault
def test_a0a_p2_send_time_check_fails_closed_when_authority_is_unavailable(tmp_path: Path) -> None:
    authority = _shared_authority(tmp_path)
    frame = queue_frame(authority, "member", "page-a", {"protected": "event"})
    moved = authority.database_path.with_suffix(".offline")
    authority.database_path.replace(moved)
    try:
        delivery = authority.authorize_frame_send(frame)
        assert delivery.delivered is False
        assert delivery.payload is None
        assert delivery.reason == "denied_or_authority_unavailable"
        with pytest.raises(AuthorityUnavailable):
            authority.counts()
    finally:
        moved.replace(authority.database_path)
    assert authority.integrity()["quick_check"] == "ok"

from __future__ import annotations

import json
from pathlib import Path

import pytest

from code_assist_v1.notion_collaboration.canonical_authority_prototype import (
    DisposableAuthority,
    InjectedFailure,
    initialize_disposable_authority,
)


def _authority(tmp_path: Path) -> DisposableAuthority:
    authority = initialize_disposable_authority(tmp_path / "atomic-realm")
    authority.create_workspace(
        "owner", "workspace-a", operation_id="op-workspace-create-atomic-0001"
    )
    authority.create_page(
        "owner",
        "workspace-a",
        "page-a",
        "Atomic",
        {"blocks": [{"id": "block-a", "text": "base"}]},
        expected_workspace_epoch=1,
        operation_id="op-page-create-atomic-0001",
    )
    return authority


@pytest.mark.parametrize(
    "fault",
    ["after_domain", "after_event", "after_audit", "after_outbox", "after_operation"],
)
@pytest.mark.integration
def test_a0a_p2_content_event_audit_outbox_and_operation_roll_back_together(
    tmp_path: Path,
    fault: str,
) -> None:
    authority = _authority(tmp_path)
    before_counts = authority.counts()
    before_page = authority.read_page(
        "owner", "page-a", expected_workspace_epoch=1, expected_page_epoch=1
    )
    operation_id = f"op-save-fault-{fault}-0001"
    with pytest.raises(InjectedFailure, match=fault):
        authority.save_page(
            "owner",
            "page-a",
            {"blocks": [{"id": "block-a", "text": fault}]},
            expected_revision=1,
            expected_workspace_epoch=1,
            expected_page_epoch=1,
            operation_id=operation_id,
            fault=fault,
        )
    assert authority.counts() == before_counts
    assert authority.operation_result("owner", "page.saved", operation_id) is None
    assert authority.event_rows(operation_id=operation_id) == []
    assert authority.outbox_rows(operation_id=operation_id) == []
    after_page = authority.read_page(
        "owner", "page-a", expected_workspace_epoch=1, expected_page_epoch=1
    )
    assert after_page == before_page
    assert authority.integrity()["quick_check"] == "ok"


@pytest.mark.integration
def test_a0a_p2_success_commits_exactly_one_operation_event_audit_and_outbox(tmp_path: Path) -> None:
    authority = _authority(tmp_path)
    before = authority.counts()
    result = authority.save_page(
        "owner",
        "page-a",
        {"blocks": [{"id": "block-a", "text": "committed"}]},
        expected_revision=1,
        expected_workspace_epoch=1,
        expected_page_epoch=1,
        operation_id="op-save-atomic-success-0001",
    )
    after = authority.counts()
    for name in ("operation_dedup", "durable_events", "audit_events", "outbox_events"):
        assert after[name] == before[name] + 1
    assert after["pages"] == before["pages"]
    assert result == authority.operation_result(
        "owner", "page.saved", "op-save-atomic-success-0001"
    )
    events = authority.event_rows(operation_id="op-save-atomic-success-0001")
    outbox = authority.outbox_rows(operation_id="op-save-atomic-success-0001")
    assert len(events) == len(outbox) == 1
    assert events[0]["event_type"] == outbox[0]["event_type"] == "page.saved"
    assert events[0]["payload_json"] == outbox[0]["payload_json"]
    assert json.loads(events[0]["payload_json"])["content_sha256"] == result["content_sha256"]


@pytest.mark.integration
def test_a0a_p2_revoke_domain_epoch_event_audit_and_fence_outbox_are_atomic(tmp_path: Path) -> None:
    authority = _authority(tmp_path)
    added = authority.add_member(
        "owner",
        "workspace-a",
        "member",
        "edit",
        expected_workspace_epoch=1,
        operation_id="op-member-add-atomic-0001",
    )
    assert added["workspace_epoch"] == 2
    member_before = authority.binding("member", "page-a")
    before = authority.counts()
    with pytest.raises(InjectedFailure):
        authority.revoke_subject(
            "owner",
            "workspace-a",
            "member",
            expected_workspace_epoch=2,
            operation_id="op-member-revoke-fault-0001",
            fault="after_outbox",
        )
    assert authority.counts() == before
    assert authority.binding("member", "page-a") == member_before
    assert authority.outbox_rows(operation_id="op-member-revoke-fault-0001") == []

    revoked = authority.revoke_subject(
        "owner",
        "workspace-a",
        "member",
        expected_workspace_epoch=2,
        operation_id="op-member-revoke-success-0001",
    )
    assert revoked["workspace_epoch"] == 3
    event = authority.event_rows(operation_id="op-member-revoke-success-0001")[0]
    outbox = authority.outbox_rows(operation_id="op-member-revoke-success-0001")[0]
    assert event["event_type"] == outbox["event_type"] == "permission.revoked"
    payload = json.loads(outbox["payload_json"])
    assert payload["gateway_fence"] == "authoritative_send_time_epoch_check"
    assert "content" not in payload and "title" not in payload

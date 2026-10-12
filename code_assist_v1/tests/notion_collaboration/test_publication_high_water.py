from __future__ import annotations

import sqlite3

import pytest

from code_assist_v1 import notion_workspace_second_brain_publication_migration as migration
from code_assist_v1 import notion_workspace_search_authority_migration as search_migration
from code_assist_v1.notion_collaboration.second_brain_boundary import (
    PublicationSourceConflict,
)
from code_assist_v1.notion_collaboration.second_brain_publication import (
    ApprovePublicationIntentCommand,
    CancelPublicationIntentCommand,
    PreparePublicationIntentCommand,
    PublicationIntentConflict,
)
from code_assist_v1.notion_collaboration.approved_dev_pair import (
    _prepare_gateway_catalog,
)

from code_assist_v1.tests.notion_collaboration.second_brain_publication_fixture import (
    ACTOR,
    environment,
    sbe_counts,
    uid,
)


pytestmark = [pytest.mark.integration, pytest.mark.scenario("PUB-001")]


def test_s0a_p2_approved_development_catalog_retains_2406_tables_at_2410(tmp_path) -> None:
    root = tmp_path / "approved-development"
    catalog = _prepare_gateway_catalog(root)
    con = sqlite3.connect(catalog)
    try:
        marker = con.execute(
            "SELECT name,checksum,actual_schema_sha256 FROM "
            "workspace_schema_migrations WHERE version=?",
            (search_migration.TARGET_VERSION,),
        ).fetchone()
        tables = {
            str(row[0])
            for row in con.execute(
                "SELECT name FROM sqlite_schema WHERE type='table'"
            ).fetchall()
        }
    finally:
        con.close()
    assert marker == (
        search_migration.TARGET_NAME,
        search_migration.TARGET_CHECKSUM,
        search_migration.TARGET_ACTUAL_SCHEMA_SHA256,
    )
    assert {
        "workspace_collaboration_publication_intents",
        "workspace_collaboration_publication_intent_events",
        "workspace_collaboration_publication_outbox",
    } <= tables
    assert search_migration.build_plan(catalog, root / "gateway-data")["state"] == (
        "already_current"
    )


def _approve(env, intent_id: str, *, label: str, target_mode: str = "both"):
    return env.coordinator.approve(
        ApprovePublicationIntentCommand(
            request_id=uid(f"{label}:request"),
            operation_id=uid(f"{label}:operation"),
            actor_user_id=ACTOR,
            actor_scope_epoch=env.actor_scope_epoch,
            intent_id=intent_id,
            target_mode=target_mode,  # type: ignore[arg-type]
            personal_space_id=(
                env.personal_space_id
                if target_mode in {"personal", "both"}
                else None
            ),
        )
    )


@pytest.mark.scenario("PUB-002")
@pytest.mark.scenario("PUB-003")
@pytest.mark.scenario("PUB-004")
def test_s0a_p2_exact_high_water_cancel_stale_replay_and_independent_targets(
    tmp_path,
    isolated_knowledge_layout,
) -> None:
    env = environment(tmp_path, isolated_knowledge_layout)
    con = sqlite3.connect(env.notion_catalog)
    try:
        marker = con.execute(
            "SELECT name,checksum,actual_schema_sha256 FROM "
            "workspace_schema_migrations WHERE version=?",
            (migration.TARGET_VERSION,),
        ).fetchone()
    finally:
        con.close()
    assert marker == (
        migration.TARGET_NAME,
        migration.TARGET_CHECKSUM,
        migration.TARGET_ACTUAL_SCHEMA_SHA256,
    )
    assert migration.SOURCE_VERSION == 2026082405
    assert migration.TARGET_VERSION == 2026082406

    canceled = env.coordinator.prepare(
        PreparePublicationIntentCommand(
            actor_user_id=ACTOR,
            page_id="page-s0a-p2",
            preview_operation_id=uid("cancel:preview"),
        )
    )
    assert canceled.state == "prepared"
    assert sbe_counts(env.sbe_factory) == {
        "publication_preview": 0,
        "publication_operation": 0,
        "knowledge_item": 0,
        "knowledge_revision": 0,
        "item_source": 0,
    }
    canceled = env.coordinator.cancel(
        CancelPublicationIntentCommand(
            actor_user_id=ACTOR,
            intent_id=canceled.intent_id,
            operation_id=uid("cancel:operation"),
        )
    )
    assert canceled.state == "canceled"
    assert canceled.terminal_reason == "user_canceled"
    assert sbe_counts(env.sbe_factory)["publication_preview"] == 0

    stale = env.coordinator.prepare(
        PreparePublicationIntentCommand(
            actor_user_id=ACTOR,
            page_id="page-s0a-p2",
            preview_operation_id=uid("stale:preview"),
        )
    )
    page = env.provider.read_document(ACTOR, "page-s0a-p2")
    env.provider.execute_typed_command(
        ACTOR,
        "page-s0a-p2",
        command_kind="block.insert",
        target_id="stale-change",
        payload={
            "type": "paragraph",
            "content": "Changed before approval",
            "after_block_id": "body",
        },
        expected_document_sequence=int(page["document_sequence"]),
        expected_workspace_epoch=int(page["workspace_epoch"]),
        expected_document_epoch=int(page["document_epoch"]),
        operation_id="op-s0a-p2-stale-change-0001",
    )
    current = env.provider.read_document(ACTOR, "page-s0a-p2")
    env.provider.create_revision_checkpoint(
        ACTOR,
        "page-s0a-p2",
        expected_revision=int(current["revision"]),
        expected_document_sequence=int(current["document_sequence"]),
        expected_workspace_epoch=int(current["workspace_epoch"]),
        expected_document_epoch=int(current["document_epoch"]),
        operation_id="op-s0a-p2-stale-checkpoint-0001",
        checkpoint_reason="manual",
    )
    with pytest.raises(PublicationSourceConflict):
        _approve(env, stale.intent_id, label="stale")
    assert sbe_counts(env.sbe_factory)["publication_preview"] == 0

    prepared = env.coordinator.prepare(
        PreparePublicationIntentCommand(
            actor_user_id=ACTOR,
            page_id="page-s0a-p2",
            preview_operation_id=uid("commit:preview"),
        )
    )
    committed = _approve(env, prepared.intent_id, label="commit")
    assert committed.state == "committed"
    assert committed.publication_result is not None
    targets = committed.publication_result["targets"]
    assert isinstance(targets, list) and len(targets) == 2
    assert {item["space_id"] for item in targets} == {
        env.personal_space_id,
        env.part_space_id,
    }
    assert len({item["item_id"] for item in targets}) == 2
    assert len({item["resolved_revision_id"] for item in targets}) == 2
    committed_counts = sbe_counts(env.sbe_factory)
    assert committed_counts["knowledge_item"] == 2
    assert committed_counts["knowledge_revision"] == 2

    replay = env.coordinator.approve(
        ApprovePublicationIntentCommand(
            request_id=uid("commit:replay-request"),
            operation_id=uid("commit:operation"),
            actor_user_id=ACTOR,
            actor_scope_epoch=env.actor_scope_epoch,
            intent_id=prepared.intent_id,
            target_mode="both",
            personal_space_id=env.personal_space_id,
        )
    )
    assert replay == committed
    assert sbe_counts(env.sbe_factory) == committed_counts
    with pytest.raises(PublicationIntentConflict):
        env.coordinator.approve(
            ApprovePublicationIntentCommand(
                request_id=uid("commit:changed-request"),
                operation_id=uid("commit:operation"),
                actor_user_id=ACTOR,
                actor_scope_epoch=env.actor_scope_epoch,
                intent_id=prepared.intent_id,
                target_mode="personal",
                personal_space_id=env.personal_space_id,
            )
        )

    page = env.provider.read_document(ACTOR, "page-s0a-p2")
    env.provider.execute_typed_command(
        ACTOR,
        "page-s0a-p2",
        command_kind="block.insert",
        target_id="after-publication",
        payload={
            "type": "paragraph",
            "content": "Never auto-synchronize",
            "after_block_id": "stale-change",
        },
        expected_document_sequence=int(page["document_sequence"]),
        expected_workspace_epoch=int(page["workspace_epoch"]),
        expected_document_epoch=int(page["document_epoch"]),
        operation_id="op-s0a-p2-post-publish-change-0001",
    )
    current = env.provider.read_document(ACTOR, "page-s0a-p2")
    env.provider.create_revision_checkpoint(
        ACTOR,
        "page-s0a-p2",
        expected_revision=int(current["revision"]),
        expected_document_sequence=int(current["document_sequence"]),
        expected_workspace_epoch=int(current["workspace_epoch"]),
        expected_document_epoch=int(current["document_epoch"]),
        operation_id="op-s0a-p2-post-publish-checkpoint-0001",
        checkpoint_reason="manual",
    )
    assert sbe_counts(env.sbe_factory) == committed_counts
    assert env.coordinator.get(
        actor_user_id=ACTOR, intent_id=prepared.intent_id
    ).publication_result == committed.publication_result

    con = sqlite3.connect(env.notion_catalog)
    try:
        event_count = int(
            con.execute(
                "SELECT count(*) FROM workspace_collaboration_publication_intent_events"
            ).fetchone()[0]
        )
        outbox_count = int(
            con.execute(
                "SELECT count(*) FROM workspace_collaboration_publication_outbox"
            ).fetchone()[0]
        )
    finally:
        con.close()
    assert event_count == outbox_count
    assert event_count >= 6

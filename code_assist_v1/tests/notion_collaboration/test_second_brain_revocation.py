from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from code_assist_v1.notion_collaboration.canonical_authority import NotFoundOrDenied
from code_assist_v1.notion_collaboration.second_brain_publication import (
    PreparePublicationIntentCommand,
    PublicationRevocationPending,
)
from code_assist_v1.tests.notion_collaboration.second_brain_publication_fixture import (
    ACTOR,
    environment,
    sbe_counts,
    uid,
)


pytestmark = [
    pytest.mark.integration,
    pytest.mark.fault,
    pytest.mark.scenario("AUTH-001"),
    pytest.mark.scenario("AUTH-002"),
    pytest.mark.scenario("AUTH-004"),
    pytest.mark.scenario("REG-002"),
    pytest.mark.scenario("REG-003"),
]

PACKAGE_PARENT = Path(__file__).resolve().parents[3]
WORKER = "code_assist_v1.tests.notion_collaboration.s0a_p2_publication_worker"


def _worker(payload: dict[str, object]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-E", "-m", WORKER],
        cwd=PACKAGE_PARENT,
        input=json.dumps(payload, ensure_ascii=False, sort_keys=True),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=30,
        check=False,
    )


def _base_payload(env) -> dict[str, object]:
    return {
        "notion_catalog": str(env.notion_catalog),
        "notion_root": str(env.notion_root),
        "sbe_database": str(env.sbe_database),
    }


def test_s0a_p2_revocation_waits_for_ambiguous_commit_then_preserves_sbe_revision(
    tmp_path,
    isolated_knowledge_layout,
) -> None:
    env = environment(tmp_path, isolated_knowledge_layout)
    pending = env.coordinator.prepare(
        PreparePublicationIntentCommand(
            actor_user_id=ACTOR,
            page_id="page-s0a-p2",
            preview_operation_id=uid("revocation:pending-preview"),
        )
    )
    committing = env.coordinator.prepare(
        PreparePublicationIntentCommand(
            actor_user_id=ACTOR,
            page_id="page-s0a-p2",
            preview_operation_id=uid("revocation:commit-preview"),
        )
    )
    approval = {
        "request_id": uid("revocation:request"),
        "operation_id": uid("revocation:operation"),
        "actor_user_id": ACTOR,
        "actor_scope_epoch": env.actor_scope_epoch,
        "intent_id": committing.intent_id,
        "target_mode": "personal",
        "personal_space_id": env.personal_space_id,
    }
    crashed = _worker(
        {
            **_base_payload(env),
            "mode": "approve_exit",
            "approval": approval,
        }
    )
    assert crashed.returncode == 23, crashed.stderr
    ambiguous = env.coordinator.get(
        actor_user_id=ACTOR,
        intent_id=committing.intent_id,
    )
    assert ambiguous.state == "sbe_committing"
    assert ambiguous.sbe_preview_id is not None
    committed_counts = sbe_counts(env.sbe_factory)
    assert committed_counts["publication_preview"] == 1
    assert committed_counts["publication_operation"] == 1
    assert committed_counts["knowledge_item"] == 1
    assert committed_counts["knowledge_revision"] == 1
    assert committed_counts["item_source"] == 1

    page = env.provider.read_document("owner", "page-s0a-p2")
    revoked = env.provider.revoke_page_grant(
        "owner",
        "page-s0a-p2",
        "subject",
        ACTOR,
        expected_workspace_epoch=int(page["workspace_epoch"]),
        expected_document_epoch=int(page["document_epoch"]),
        operation_id="op-s0a-p2-revoke-editor-0001",
    )
    assert revoked["state"] == "revoked"
    with pytest.raises(NotFoundOrDenied):
        env.provider.capture_bound(actor_user_id=ACTOR, page_id="page-s0a-p2")
    with pytest.raises(NotFoundOrDenied):
        env.coordinator.prepare(
            PreparePublicationIntentCommand(
                actor_user_id=ACTOR,
                page_id="page-s0a-p2",
                preview_operation_id=uid("revocation:denied-preview"),
            )
        )

    revoke_operation = uid("revocation:completion")
    with pytest.raises(PublicationRevocationPending) as blocked:
        env.coordinator.complete_source_revocation(
            actor_user_id=ACTOR,
            page_id="page-s0a-p2",
            operation_id=revoke_operation,
        )
    assert blocked.value.intent_ids == (committing.intent_id,)
    assert env.coordinator.get(
        actor_user_id=ACTOR, intent_id=pending.intent_id
    ).state == "prepared"

    recovered = _worker(
        {
            **_base_payload(env),
            "mode": "recover",
            "actor_user_id": ACTOR,
            "intent_id": committing.intent_id,
        }
    )
    assert recovered.returncode == 0, recovered.stderr
    recovered_record = json.loads(recovered.stdout)
    assert recovered_record["state"] == "committed"
    assert sbe_counts(env.sbe_factory) == committed_counts

    completed = env.coordinator.complete_source_revocation(
        actor_user_id=ACTOR,
        page_id="page-s0a-p2",
        operation_id=revoke_operation,
    )
    assert completed["revocation_complete"] is True
    assert completed["canceled_prepared_intent_ids"] == [pending.intent_id]
    assert completed["committed_intent_ids_unchanged"] == [committing.intent_id]
    assert completed["unresolved_intent_ids"] == []
    assert env.coordinator.get(
        actor_user_id=ACTOR, intent_id=pending.intent_id
    ).terminal_reason == "source_authority_revoked"

    replayed = _worker(
        {
            **_base_payload(env),
            "mode": "recover",
            "actor_user_id": ACTOR,
            "intent_id": committing.intent_id,
        }
    )
    assert replayed.returncode == 0, replayed.stderr
    assert json.loads(replayed.stdout) == recovered_record
    assert sbe_counts(env.sbe_factory) == committed_counts

    result = recovered_record["publication_result"]
    target = result["targets"][0]
    con = env.sbe_factory.open(read_only=True)
    try:
        durable = con.execute(
            "SELECT item.id,item.space_id,item.lifecycle_status,item.curation_status,"
            "item.current_revision_id,revision.title,revision.raw_content_sha256,"
            "artifact.origin_type,artifact.origin_key "
            "FROM knowledge_item item "
            "JOIN knowledge_revision revision ON revision.id=item.current_revision_id "
            "JOIN item_source source ON source.item_id=item.id "
            "JOIN source_artifact artifact ON artifact.id=source.artifact_id "
            "WHERE item.id=? AND revision.id=?",
            (target["item_id"], target["resolved_revision_id"]),
        ).fetchone()
    finally:
        con.close()
    assert durable is not None
    assert tuple(durable[:4]) == (
        target["item_id"],
        env.personal_space_id,
        "active",
        "provisional",
    )
    assert durable[4] == target["resolved_revision_id"]
    assert durable[5] == "Durable publication"
    assert durable[7] == "notion"
    assert durable[8] == "notion:owner:page-s0a-p2"

    notion = sqlite3.connect(env.notion_catalog)
    try:
        states = dict(
            notion.execute(
                "SELECT id,state FROM workspace_collaboration_publication_intents"
            ).fetchall()
        )
        event_count, outbox_count = notion.execute(
            "SELECT "
            "(SELECT count(*) FROM workspace_collaboration_publication_intent_events),"
            "(SELECT count(*) FROM workspace_collaboration_publication_outbox)"
        ).fetchone()
    finally:
        notion.close()
    assert states[pending.intent_id] == "canceled"
    assert states[committing.intent_id] == "committed"
    assert event_count == outbox_count

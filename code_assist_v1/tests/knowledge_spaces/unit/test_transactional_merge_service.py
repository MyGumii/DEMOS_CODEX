from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from code_assist_v1.knowledge_core.db import transaction
from code_assist_v1.knowledge_core.scope import ScopeResolver
from code_assist_v1.knowledge_core.services.editing import LeaseHmacKeyRing
from code_assist_v1.knowledge_core.services.editing import LeaseIdempotencyError, LeaseLostError
from code_assist_v1.knowledge_core.services.merges import (
    MergeApplyCommand,
    MergePreviewCommand,
    MergePreviewStaleError,
    TransactionalMergeService,
)
from code_assist_v1.tests.knowledge_spaces.unit.test_item_edit_service import (
    _Fixture,
    _KEY,
    _acquire,
    _assert_postconditions,
    _flags,
    _make_published,
    _publish,
    _save_command,
    _seed,
    _uuid,
)


pytestmark = pytest.mark.unit


def _merge_fixture(layout):
    source = _seed(layout)
    _make_published(source, label="merge-source")
    target_item_id, target_revision_id = _publish(
        source.factory,
        ScopeResolver(_flags()),
        epoch=source.epochs["editor"],
        label="merge-target",
        markdown="# Canonical target\nTarget retained material.\n",
    )
    target = _Fixture(
        source.factory,
        source.part_id,
        source.space_id,
        target_item_id,
        target_revision_id,
        source.epochs,
    )
    _make_published(target, label="merge-target")
    edge_id = _uuid("merge-existing-edge")
    connection = source.factory.open()
    try:
        with transaction(connection):
            connection.execute(
                """INSERT INTO knowledge_edge(
                     id,space_id,source_item_id,target_item_id,edge_type,edge_status,
                     origin,source_evidence_revision_id,target_evidence_revision_id,
                     approval_finding_id,evidence_json,needs_revalidation,created_by,
                     updated_by,created_at,updated_at,row_version
                   ) VALUES (?,?,?,?,?,'active','manual',?,?,NULL,'{}',0,
                     'editor','editor',500,500,1)""",
                (
                    edge_id,
                    source.space_id,
                    source.item_id,
                    target.item_id,
                    "related",
                    source.revision_id,
                    target.revision_id,
                ),
            )
    finally:
        connection.close()
    return source, target, edge_id


def _service(fixture: _Fixture, *, failpoint=None) -> TransactionalMergeService:
    return TransactionalMergeService(
        fixture.factory,
        key_ring=LeaseHmacKeyRing(
            active_key_id="lease-v1", keys={"lease-v1": _KEY}
        ),
        scope_resolver=ScopeResolver(_flags()),
        failpoint=failpoint,
    )


def _preview(service, source: _Fixture, target: _Fixture, *, result=None):
    return service.preview(
        MergePreviewCommand(
            principal=source.principal("editor"),
            space_id=source.space_id,
            source_item_id=source.item_id,
            source_revision_id=source.revision_id,
            target_item_id=target.item_id,
            target_revision_id=target.revision_id,
            result_markdown=result
            or "# Canonical target\nTarget retained material.\nSource contribution.\n",
            request_id=_uuid("merge-preview-request"),
        )
    )


def _command(
    source: _Fixture,
    target: _Fixture,
    preview,
    source_acquire,
    source_lease,
    target_acquire,
    target_lease,
) -> MergeApplyCommand:
    return MergeApplyCommand(
        principal=source.principal("editor"),
        space_id=source.space_id,
        source_item_id=source.item_id,
        source_revision_id=source.revision_id,
        target_item_id=target.item_id,
        target_revision_id=target.revision_id,
        result_markdown=preview.panes[2].markdown,
        preview_sha256=preview.preview_sha256,
        source_editor_session_id=source_acquire.editor_session_id,
        source_lease_id=source_lease.lease_id,
        source_fencing_generation=source_lease.fencing_generation,
        source_raw_token=source_acquire.raw_token,
        target_editor_session_id=target_acquire.editor_session_id,
        target_lease_id=target_lease.lease_id,
        target_fencing_generation=target_lease.fencing_generation,
        target_raw_token=target_acquire.raw_token,
        operation_id=_uuid("merge-apply-operation"),
        request_id=_uuid("merge-apply-request"),
        request_meta={"method": "POST", "authorization": "Bearer secret"},
    )


def _acquire_pair(source: _Fixture, target: _Fixture):
    _, source_command, source_lease = _acquire(source, label="merge-source")
    _, target_command, target_lease = _acquire(target, label="merge-target")
    return source_command, source_lease, target_command, target_lease


def _state(source: _Fixture, target: _Fixture, edge_id: str) -> dict[str, object]:
    connection = source.factory.open(read_only=True)
    try:
        source_row = connection.execute(
            """SELECT lifecycle_status,redirect_item_id,current_revision_id,row_version
                 FROM knowledge_item WHERE id=?""",
            (source.item_id,),
        ).fetchone()
        target_row = connection.execute(
            """SELECT lifecycle_status,curation_status,current_revision_id,
                      last_curation_transition_id,row_version
                 FROM knowledge_item WHERE id=?""",
            (target.item_id,),
        ).fetchone()
        edge = connection.execute(
            """SELECT source_item_id,target_item_id,edge_type,edge_status,
                      source_evidence_revision_id,target_evidence_revision_id,
                      needs_revalidation,row_version
                 FROM knowledge_edge WHERE id=?""",
            (edge_id,),
        ).fetchone()
        relation = connection.execute(
            """SELECT source_item_id,source_revision_id,target_item_id,
                      target_revision_id,relation_type,relation_status
                 FROM item_relation WHERE relation_type='merged_into'"""
        ).fetchone()
        event = connection.execute(
            """SELECT source_item_id,source_revision_id,target_item_id,
                      target_revision_id,result_revision_id,preview_sha256,relation_id
                 FROM merge_event"""
        ).fetchone()
        revision = None
        if event is not None:
            revision = connection.execute(
                """SELECT change_kind,parent_revision_id,base_revision_id,
                          raw_content_sha256,normalizer_version
                     FROM knowledge_revision WHERE id=?""",
                (event[4],),
            ).fetchone()
        return {
            "audit": int(connection.execute("SELECT count(*) FROM audit_event").fetchone()[0]),
            "edge": None if edge is None else tuple(edge),
            "event": None if event is None else tuple(event),
            "jobs": int(connection.execute("SELECT count(*) FROM durable_job").fetchone()[0]),
            "leases": tuple(
                tuple(row)
                for row in connection.execute(
                    """SELECT item_id,holder_user_id,fencing_generation
                         FROM item_lease WHERE item_id IN (?,?) ORDER BY item_id""",
                    (source.item_id, target.item_id),
                ).fetchall()
            ),
            "operations": int(
                connection.execute("SELECT count(*) FROM operation_dedup").fetchone()[0]
            ),
            "outbox": int(connection.execute("SELECT count(*) FROM outbox_event").fetchone()[0]),
            "relation": None if relation is None else tuple(relation),
            "revision": None if revision is None else tuple(revision),
            "search": int(
                connection.execute(
                    "SELECT count(*) FROM search_document WHERE item_id IN (?,?)",
                    (source.item_id, target.item_id),
                ).fetchone()[0]
            ),
            "source": tuple(source_row),
            "target": tuple(target_row),
        }
    finally:
        connection.close()


@pytest.mark.scenario("MERGE-001")
@pytest.mark.scenario("MERGE-002")
@pytest.mark.scenario("MERGE-003")
def test_reviewed_merge_is_dual_fenced_atomic_idempotent_and_never_rewires_edges(
    isolated_knowledge_layout,
) -> None:
    source, target, edge_id = _merge_fixture(isolated_knowledge_layout)
    service = _service(source)
    preview = _preview(service, source, target)
    assert [pane.label for pane in preview.panes] == [
        "merge_source",
        "merge_target",
        "result",
    ]
    assert preview.edge_impact["edge_ids"] == [edge_id]
    assert preview.edge_impact["policy"] == "inactive_needs_revalidation_no_rewire"
    acquired = _acquire_pair(source, target)
    command = _command(source, target, preview, *acquired)
    before = _state(source, target, edge_id)

    result = service.apply(command)
    replay_request = _uuid("merge-replay-request")
    replay = service.apply(replace(command, request_id=replay_request))
    assert replay == replace(result, request_id=replay_request)

    after = _state(source, target, edge_id)
    assert after["source"] == ("merged", target.item_id, source.revision_id, 3)
    assert after["target"][0:2] == ("active", "provisional")
    assert after["target"][2] == result.result_revision_id
    assert after["target"][4] == 3
    assert after["revision"][0:3] == (
        "merge",
        target.revision_id,
        target.revision_id,
    )
    assert after["revision"][4] == "markdown_lf_v1"
    assert after["relation"] == (
        source.item_id,
        source.revision_id,
        target.item_id,
        result.result_revision_id,
        "merged_into",
        "active",
    )
    assert after["event"][0:5] == (
        source.item_id,
        source.revision_id,
        target.item_id,
        target.revision_id,
        result.result_revision_id,
    )
    assert after["event"][5] == preview.preview_sha256
    assert after["edge"] == (
        source.item_id,
        target.item_id,
        "related",
        "inactive",
        source.revision_id,
        target.revision_id,
        1,
        2,
    )
    assert after["search"] == 0
    assert all(row[1] is None for row in after["leases"])
    assert after["audit"] == before["audit"] + 1
    assert after["outbox"] == before["outbox"] + 1
    assert after["jobs"] == before["jobs"] + 1
    assert after["operations"] == before["operations"] + 2
    _assert_postconditions(source.factory)


def test_changed_confirmed_markdown_or_preview_hash_is_stale_without_effect(
    isolated_knowledge_layout,
) -> None:
    source, target, edge_id = _merge_fixture(isolated_knowledge_layout)
    service = _service(source)
    preview = _preview(service, source, target)
    acquired = _acquire_pair(source, target)
    command = _command(source, target, preview, *acquired)
    baseline = _state(source, target, edge_id)

    with pytest.raises(MergePreviewStaleError):
        service.apply(replace(command, result_markdown=command.result_markdown + "changed"))
    assert _state(source, target, edge_id) == baseline


@pytest.mark.scenario("MERGE-004")
def test_changed_fixed_target_revision_makes_preview_stale_and_never_merges_source(
    isolated_knowledge_layout,
) -> None:
    source, target, edge_id = _merge_fixture(isolated_knowledge_layout)
    merge_service = _service(source)
    preview = _preview(merge_service, source, target)
    source_edit_service, source_acquire, source_lease = _acquire(
        source, label="stale-merge-source"
    )
    target_edit_service, target_acquire, target_lease = _acquire(
        target, label="stale-merge-target"
    )
    del source_edit_service
    command = _command(
        source,
        target,
        preview,
        source_acquire,
        source_lease,
        target_acquire,
        target_lease,
    )
    target_edit_service.save(
        _save_command(
            target,
            target_acquire,
            target_lease,
            label="stale-merge-target",
            expected_revision_id=target.revision_id,
            expected_row_version=2,
        )
    )
    after_edit = _state(source, target, edge_id)

    with pytest.raises(MergePreviewStaleError):
        merge_service.apply(command)
    assert _state(source, target, edge_id) == after_edit
    assert after_edit["source"][0] == "active"
    assert after_edit["event"] is None
    assert after_edit["relation"] is None


def test_merge_requires_both_exact_tokens_and_replays_the_terminal_lock_error(
    isolated_knowledge_layout,
) -> None:
    source, target, edge_id = _merge_fixture(isolated_knowledge_layout)
    service = _service(source)
    preview = _preview(service, source, target)
    acquired = _acquire_pair(source, target)
    command = _command(source, target, preview, *acquired)
    bad = replace(command, target_raw_token=command.source_raw_token)
    baseline = _state(source, target, edge_id)

    with pytest.raises(LeaseLostError) as first:
        service.apply(bad)
    with pytest.raises(LeaseLostError) as replay:
        service.apply(replace(bad, request_id=_uuid("bad-lock-replay")))
    assert str(replay.value) == str(first.value)
    state = _state(source, target, edge_id)
    assert state["event"] is None
    assert state["relation"] is None
    assert state["source"][0] == "active"
    assert state["target"][2] == target.revision_id
    assert state["jobs"] == baseline["jobs"]


def test_committed_operation_id_cannot_be_rebound_to_other_content(
    isolated_knowledge_layout,
) -> None:
    source, target, edge_id = _merge_fixture(isolated_knowledge_layout)
    service = _service(source)
    preview = _preview(service, source, target)
    acquired = _acquire_pair(source, target)
    command = _command(source, target, preview, *acquired)
    service.apply(command)
    baseline = _state(source, target, edge_id)

    with pytest.raises(LeaseIdempotencyError):
        service.apply(
            replace(
                command,
                result_markdown=command.result_markdown + "different",
                request_id=_uuid("merge-idempotency-rebind"),
            )
        )
    assert _state(source, target, edge_id) == baseline


class _ObservedMergeService(TransactionalMergeService):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.validation_order: list[str] = []

    def validate_edit_fence_in_transaction(self, connection, check):
        self.validation_order.append(check.item_id)
        return super().validate_edit_fence_in_transaction(connection, check)


def test_dual_fences_are_validated_in_sorted_item_id_order(
    isolated_knowledge_layout,
) -> None:
    source, target, _edge_id = _merge_fixture(isolated_knowledge_layout)
    service = _ObservedMergeService(
        source.factory,
        key_ring=LeaseHmacKeyRing(
            active_key_id="lease-v1", keys={"lease-v1": _KEY}
        ),
        scope_resolver=ScopeResolver(_flags()),
    )
    preview = _preview(service, source, target)
    acquired = _acquire_pair(source, target)
    service.apply(_command(source, target, preview, *acquired))
    assert service.validation_order == sorted((source.item_id, target.item_id))


@pytest.mark.parametrize(
    "failpoint",
    (
        "merge.after_dual_acquire",
        "merge.after_rescan_enqueue",
        "merge.after_result_revision",
        "merge.after_source_state",
        "merge.after_redirect",
        "merge.after_dual_release",
        "merge.after_audit",
        "merge.after_outbox",
        "merge.before_commit",
    ),
)
def test_every_merge_failpoint_rolls_back_both_items_and_all_effects(
    isolated_knowledge_layout,
    failpoint: str,
) -> None:
    source, target, edge_id = _merge_fixture(isolated_knowledge_layout)

    def stop(name: str) -> None:
        if name == failpoint:
            raise RuntimeError(name)

    preview = _preview(_service(source), source, target)
    acquired = _acquire_pair(source, target)
    command = _command(source, target, preview, *acquired)
    baseline = _state(source, target, edge_id)
    with pytest.raises(RuntimeError, match=failpoint):
        _service(source, failpoint=stop).apply(command)
    assert _state(source, target, edge_id) == baseline
    _assert_postconditions(source.factory)


def test_merge_audit_outbox_and_dedup_never_persist_raw_lease_tokens(
    isolated_knowledge_layout,
) -> None:
    source, target, _edge_id = _merge_fixture(isolated_knowledge_layout)
    service = _service(source)
    preview = _preview(service, source, target)
    acquired = _acquire_pair(source, target)
    command = _command(source, target, preview, *acquired)
    service.apply(command)

    connection = source.factory.open(read_only=True)
    try:
        payload = "\n".join(
            str(value)
            for row in connection.execute(
                """SELECT request_meta_json,details_json FROM audit_event
                   UNION ALL SELECT payload_json,NULL FROM outbox_event
                   UNION ALL SELECT result_json,error_json FROM operation_dedup"""
            ).fetchall()
            for value in row
            if value is not None
        )
    finally:
        connection.close()
    assert command.source_raw_token not in payload
    assert command.target_raw_token not in payload
    assert "Bearer secret" not in payload

"""UI-SYNC-LEGACY-JOIN-PROCESS: two independent writers leave original untouched."""
import multiprocessing

import pytest

from code_assist_v1.notion_collaboration import synced_legacy_adoption_commit as commit
from code_assist_v1.notion_collaboration import synced_legacy_adoption_service as service
from code_assist_v1.tests.notion_collaboration.test_synced_legacy_adoption_cancel_process import _writer
from code_assist_v1.tests.notion_collaboration.test_synced_legacy_join import (
    environment, member_environment, native_environment, archive_environment,
    guest_environment, image_environment, initial, insert_legacy, create, selection,
    reviewed_body, inspect,
)


@pytest.mark.parametrize("winner", ["adopt", "cancel"])
def test_ui_legacy_join_two_processes_share_one_outcome_without_changing_original(environment, winner):
    store, ws = environment
    _child, _first = initial(store, ws)
    later = create(store, ws, operation="legacy-join-process-later")["page"]["id"]
    insert_legacy(store, later, "later-member")
    selected = selection(store, ws, later)
    body = reviewed_body(selected, operation="legacy-join-process-race")
    service.register_legacy_adoption_intent(store.iam, "owner", ws, **body)
    delta = commit.prepare_adoption_delta(store.iam, selected)
    before, counts = inspect(store, delta)
    context = multiprocessing.get_context("spawn")
    barrier, entered, output = context.Barrier(2), context.Event(), context.Queue()
    workers = [context.Process(target=_writer, args=(store.db_path, store.user_data_root, ws, body,
        delta, role, winner, barrier, entered, output)) for role in ("adopt", "cancel")]
    try:
        for worker in workers: worker.start()
        results = [output.get(timeout=55), output.get(timeout=55)]
        for worker in workers:
            worker.join(10); assert worker.exitcode == 0
        assert all("error" not in item for item in results), results
        expected = "committed" if winner == "adopt" else "cancelled"
        assert [item["state"] for item in results] == [expected, expected]
        assert sorted(item["result"]["replayed"] for item in results) == [False, True]
        assert {**results[0]["result"], "replayed": False} == {**results[1]["result"], "replayed": False}
        after, updated = inspect(store, delta)
        assert before["page-e1"] == after["page-e1"]
        assert updated["workspace_collaboration_revisions"] == counts["workspace_collaboration_revisions"] + (1 if expected == "committed" else 0)
        if expected == "cancelled": assert before[later] == after[later]
    finally:
        entered.set()
        for worker in workers:
            if worker.is_alive(): worker.terminate()
            worker.join(5)
        output.close()

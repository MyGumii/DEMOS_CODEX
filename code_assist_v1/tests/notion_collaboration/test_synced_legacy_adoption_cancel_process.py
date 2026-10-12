"""UI-SYNC-LEGACY-CANCEL: independent Windows writers share one terminal fence."""
from contextlib import contextmanager
import multiprocessing

import pytest

from code_assist_v1.notion_collaboration import synced_blocks as synced
from code_assist_v1.notion_collaboration import synced_legacy_adoption_commit as commit
from code_assist_v1.notion_collaboration import synced_legacy_adoption_service as service
from code_assist_v1.tests.notion_collaboration.test_synced_legacy_adoption import (
    environment, member_environment, native_environment, archive_environment,
    guest_environment, image_environment, setup_selection, prepare,
)


def _writer(catalog, root, ws, body, delta, role, winner, barrier, entered, output):
    from code_assist_v1.notion_collaboration.iam_authority import InMemoryIdentityDirectory
    from code_assist_v1.notion_collaboration.iam_runtime import CollaborativeWorkspaceStore
    store = CollaborativeWorkspaceStore(catalog, user_data_root=root, identity_directory=InMemoryIdentityDirectory(["owner"]))
    transaction = store.iam._transaction
    @contextmanager
    def coordinated():
        barrier.wait(timeout=40)
        if role != winner and not entered.wait(40):
            raise RuntimeError("writer start timeout")
        with transaction() as con:
            if role == winner:
                entered.set()
            yield con
    store.iam._transaction = coordinated
    try:
        if role == "adopt":
            value = commit.commit_prepared_adoption(store.iam, delta,
                operation_id=body["operation_id"], require_intent=True)
            output.put({"state": value.get("state", "committed"), "result": value})
        else:
            output.put(service.cancel_legacy_adoption(store.iam, "owner", ws, **body))
    except Exception as exc:
        output.put({"error": type(exc).__name__, "message": str(exc)})


@pytest.mark.parametrize("winner", ["adopt", "cancel"])
def test_ui_legacy_adopt_and_cancel_two_processes_share_one_outcome(environment, winner):
    store, ws = environment
    child = setup_selection(store, ws)
    selected = prepare(store, ws, child)
    body = {"origin": selected.value()["origin"], "references": selected.value()["references"],
        "preparation_sha256": selected.payload_sha256, "operation_id": "legacy-cancel-two-process"}
    service.register_legacy_adoption_intent(store.iam, "owner", ws, **body)
    delta = commit.prepare_adoption_delta(store.iam, selected)
    with store.iam._reader() as con:
        before = con.execute("SELECT COUNT(*) FROM workspace_collaboration_revisions").fetchone()[0]
    context = multiprocessing.get_context("spawn")
    barrier, entered, output = context.Barrier(2), context.Event(), context.Queue()
    workers = [context.Process(target=_writer, args=(store.db_path, store.user_data_root, ws, body,
        delta, role, winner, barrier, entered, output)) for role in ("adopt", "cancel")]
    try:
        for worker in workers:
            worker.start()
        results = [output.get(timeout=55), output.get(timeout=55)]
        for worker in workers:
            worker.join(10)
            assert worker.exitcode == 0
        assert all("error" not in item for item in results), results
        expected = "committed" if winner == "adopt" else "cancelled"
        assert [item["state"] for item in results] == [expected, expected]
        assert sorted(item["result"]["replayed"] for item in results) == [False, True]
        assert {**results[0]["result"], "replayed": False} == {**results[1]["result"], "replayed": False}
        with store.iam._reader() as con:
            assert con.execute("SELECT COUNT(*) FROM workspace_collaboration_revisions").fetchone()[0] == before + (2 if expected == "committed" else 0)
            assert con.execute("SELECT COUNT(*) FROM workspace_collaboration_operations WHERE action=? AND operation_id=?",
                (commit.ACTION, body["operation_id"])).fetchone()[0] == 1
        status = service.read_legacy_adoption_result(store.iam, "owner", ws, **body)
        assert status["state"] == expected
        if expected == "committed":
            assert synced.read_synced_block(store.iam, "owner", child, "chosen-instance")["block"]["content"] == "조합된 한글 A"
        else:
            assert prepare(store, ws, child).payload_sha256 == selected.payload_sha256
    finally:
        entered.set()
        for worker in workers:
            if worker.is_alive(): worker.terminate()
            worker.join(5)
        output.close()

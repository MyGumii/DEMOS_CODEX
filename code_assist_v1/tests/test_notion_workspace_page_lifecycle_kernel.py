from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

import pytest

from code_assist_v1 import notion_workspace_page_tree as page_tree
from code_assist_v1 import notion_workspace_store as store_module
from code_assist_v1.notion_workspace_store import (
    PAGE_LIFECYCLE_FINALIZED_ACTION,
    WorkspacePageLifecycleCommandRequest,
    WorkspacePageLifecycleLeaseConflict,
    WorkspacePageLifecycleSourceConflict,
    WorkspacePageLifecycleSourceReceipt,
    WorkspacePageTreeSourceFenceConflict,
    WorkspaceSubstrateUnavailable,
)
from code_assist_v1.tests.test_notion_workspace_page_tree import _prepare, _run_args


LEASE_TOKEN = "pllease_" + "a" * 32
RECEIPT = WorkspacePageLifecycleSourceReceipt(
    source_receipt_sha256="sha256:" + "b" * 64,
    affected_node_count=1,
    file_work_count=0,
)


def _materialized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[dict[str, object], WorkspacePageLifecycleCommandRequest]:
    prepared = _prepare(tmp_path, monkeypatch)
    page_tree.run_offline_page_tree_materialization(
        prepared["store"], **_run_args(prepared)
    )
    store = prepared["store"]
    workspace_id = str(prepared["workspace_id"])
    context = prepared["context"]
    binding = next(
        item
        for item in context["bindings"]
        if item["source_page_id"] == prepared["child"]
    )
    workspace = store.get_workspace("reviewer", workspace_id)
    request = WorkspacePageLifecycleCommandRequest(
        workspace_id=workspace_id,
        action="rename",
        source_owner_subject_id="owner",
        source_page_id=str(prepared["child"]),
        expected_tree_version=1,
        expected_binding_version=int(binding["binding_version"]),
        expected_incarnation_generation=int(
            binding["source_incarnation_generation"]
        ),
        base_version=int(workspace["version"]),
    )
    return prepared, request


def _source_applied(prepared, request, *, operation_id: str):
    store = prepared["store"]
    created = store.prepare_page_lifecycle_command(
        "reviewer", request, operation_id=operation_id
    )
    store.claim_page_lifecycle_command(
        "reviewer",
        request.workspace_id,
        created["command_id"],
        operation_id=operation_id,
        worker="worker.1",
        lease_token=LEASE_TOKEN,
        lease_seconds=120,
        expected_command_version=1,
    )
    applied = store.record_page_lifecycle_source_applied(
        "reviewer",
        request.workspace_id,
        created["command_id"],
        RECEIPT,
        operation_id=operation_id,
        worker="worker.1",
        lease_token=LEASE_TOKEN,
        fencing_generation=1,
        expected_command_version=2,
        source_revalidator=lambda candidate: candidate == RECEIPT,
    )
    return created, applied


def test_disabled_rename_kernel_converges_source_response_and_rejects_v1_finalize(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepared, request = _materialized(tmp_path, monkeypatch)
    store = prepared["store"]
    operation_id = "rename-lifecycle-1"

    created = store.prepare_page_lifecycle_command(
        "reviewer", request, operation_id=operation_id
    )
    assert created["status"] == "prepared"
    assert created["command_version"] == 1
    assert store.prepare_page_lifecycle_command(
        "reviewer", request, operation_id=operation_id
    ) == created

    con = store._read_connection()
    try:
        assert con.execute(
            "SELECT COUNT(*) FROM workspace_operations WHERE operation_id=?",
            (operation_id,),
        ).fetchone()[0] == 0
        assert con.execute(
            "SELECT COUNT(*) FROM workspace_audit_events WHERE operation_id=?",
            (operation_id,),
        ).fetchone()[0] == 0
        assert con.execute(
            "SELECT COUNT(*) FROM workspace_outbox WHERE operation_id=?",
            (operation_id,),
        ).fetchone()[0] == 0
    finally:
        con.close()

    claimed = store.claim_page_lifecycle_command(
        "reviewer",
        request.workspace_id,
        created["command_id"],
        operation_id=operation_id,
        worker="worker.1",
        lease_token=LEASE_TOKEN,
        lease_seconds=120,
        expected_command_version=1,
    )
    assert claimed["status"] == "prepared"
    assert claimed["command_version"] == 2
    assert claimed["fencing_generation"] == 1
    assert store.claim_page_lifecycle_command(
        "reviewer",
        request.workspace_id,
        created["command_id"],
        operation_id=operation_id,
        worker="worker.1",
        lease_token=LEASE_TOKEN,
        lease_seconds=120,
        expected_command_version=1,
    ) == claimed

    source_revalidations = 0

    def source_revalidator(candidate):
        nonlocal source_revalidations
        source_revalidations += 1
        return candidate == RECEIPT

    source_applied = store.record_page_lifecycle_source_applied(
        "reviewer",
        request.workspace_id,
        created["command_id"],
        RECEIPT,
        operation_id=operation_id,
        worker="worker.1",
        lease_token=LEASE_TOKEN,
        fencing_generation=1,
        expected_command_version=2,
        source_revalidator=source_revalidator,
    )
    assert source_applied["status"] == "source_applied"
    assert source_applied["command_version"] == 3
    assert source_revalidations == 1

    replayed_source = store.record_page_lifecycle_source_applied(
        "reviewer",
        request.workspace_id,
        created["command_id"],
        RECEIPT,
        operation_id=operation_id,
        worker="worker.1",
        lease_token=LEASE_TOKEN,
        fencing_generation=1,
        expected_command_version=2,
        source_revalidator=lambda _candidate: (_ for _ in ()).throw(
            AssertionError("source revalidation ran during response-loss replay")
        ),
    )
    assert replayed_source == source_applied
    assert source_revalidations == 1

    with pytest.raises(
        sqlite3.IntegrityError,
        match="workspace lifecycle operation namespace conflict",
    ):
        store.finalize_page_lifecycle_command(
            "reviewer",
            request.workspace_id,
            created["command_id"],
            operation_id=operation_id,
            worker="worker.1",
            lease_token=LEASE_TOKEN,
            fencing_generation=1,
            expected_command_version=3,
            source_revalidator=lambda candidate: candidate == RECEIPT,
        )

    con = store._read_connection()
    try:
        row = con.execute(
            "SELECT status,row_version FROM workspace_page_lifecycle_commands "
            "WHERE id=?",
            (created["command_id"],),
        ).fetchone()
        assert tuple(row) == ("source_applied", 3)
        for table in ("workspace_operations", "workspace_audit_events", "workspace_outbox"):
            assert con.execute(
                f"SELECT COUNT(*) FROM {table} WHERE operation_id=?",
                (operation_id,),
            ).fetchone()[0] == 0
    finally:
        con.close()
    assert store.deep_health()["healthy"] is True


def test_stale_lease_is_write_free_and_manual_audit_replays(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepared, request = _materialized(tmp_path, monkeypatch)
    store = prepared["store"]
    operation_id = "rename-lifecycle-terminal"
    created = store.prepare_page_lifecycle_command(
        "reviewer", request, operation_id=operation_id
    )
    claimed = store.claim_page_lifecycle_command(
        "reviewer",
        request.workspace_id,
        created["command_id"],
        operation_id=operation_id,
        worker="worker.1",
        lease_token=LEASE_TOKEN,
        lease_seconds=120,
        expected_command_version=1,
    )
    with pytest.raises(WorkspacePageLifecycleLeaseConflict):
        store.claim_page_lifecycle_command(
            "reviewer",
            request.workspace_id,
            created["command_id"],
            operation_id=operation_id,
            worker="worker.2",
            lease_token="pllease_" + "c" * 32,
            lease_seconds=120,
            expected_command_version=claimed["command_version"],
        )
    terminal = store.terminate_page_lifecycle_command(
        "reviewer",
        request.workspace_id,
        created["command_id"],
        operation_id=operation_id,
        worker="worker.1",
        lease_token=LEASE_TOKEN,
        fencing_generation=1,
        expected_command_version=2,
        disposition="manual_audit",
        failure_code="source_outcome_ambiguous",
    )
    assert terminal["status"] == "manual_audit"
    assert terminal["failure_code"] == "source_outcome_ambiguous"
    assert store.terminate_page_lifecycle_command(
        "reviewer",
        request.workspace_id,
        created["command_id"],
        operation_id=operation_id,
        worker="worker.1",
        lease_token=LEASE_TOKEN,
        fencing_generation=1,
        expected_command_version=2,
        disposition="manual_audit",
        failure_code="source_outcome_ambiguous",
    ) == terminal
    con = store._read_connection()
    try:
        assert con.execute(
            "SELECT COUNT(*) FROM workspace_operations WHERE operation_id=?",
            (operation_id,),
        ).fetchone()[0] == 0
    finally:
        con.close()
    assert store.deep_health()["healthy"] is True


def test_finalize_outbox_fault_rolls_back_command_and_all_ledgers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepared, request = _materialized(tmp_path, monkeypatch)
    store = prepared["store"]
    operation_id = "rename-lifecycle-finalize-fault"
    created, _applied = _source_applied(
        prepared, request, operation_id=operation_id
    )
    real_write_connection = store._write_connection

    def faulting_write_connection():
        con = real_write_connection()
        con.execute(
            """
            CREATE TEMP TRIGGER test_lifecycle_outbox_fault
            BEFORE INSERT ON workspace_outbox
            WHEN NEW.event_type='workspace.page_lifecycle_finalized'
            BEGIN
              SELECT RAISE(ABORT, 'injected lifecycle outbox fault');
            END
            """
        )
        return con

    monkeypatch.setattr(store, "_write_connection", faulting_write_connection)
    with pytest.raises(sqlite3.IntegrityError, match="injected lifecycle outbox fault"):
        store.finalize_page_lifecycle_command(
            "reviewer",
            request.workspace_id,
            created["command_id"],
            operation_id=operation_id,
            worker="worker.1",
            lease_token=LEASE_TOKEN,
            fencing_generation=1,
            expected_command_version=3,
            source_revalidator=lambda candidate: candidate == RECEIPT,
        )
    monkeypatch.setattr(store, "_write_connection", real_write_connection)

    con = store._read_connection()
    try:
        command = con.execute(
            "SELECT status,row_version FROM workspace_page_lifecycle_commands WHERE id=?",
            (created["command_id"],),
        ).fetchone()
        assert (command["status"], command["row_version"]) == ("source_applied", 3)
        for table in (
            "workspace_operations",
            "workspace_audit_events",
            "workspace_outbox",
        ):
            assert con.execute(
                f"SELECT COUNT(*) FROM {table} WHERE operation_id=?",
                (operation_id,),
            ).fetchone()[0] == 0
    finally:
        con.close()

    with pytest.raises(
        sqlite3.IntegrityError,
        match="workspace lifecycle operation namespace conflict",
    ):
        store.finalize_page_lifecycle_command(
            "reviewer",
            request.workspace_id,
            created["command_id"],
            operation_id=operation_id,
            worker="worker.1",
            lease_token=LEASE_TOKEN,
            fencing_generation=1,
            expected_command_version=3,
            source_revalidator=lambda candidate: candidate == RECEIPT,
        )


def test_catalog_fence_drift_blocks_finalize_before_source_revalidation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepared, request = _materialized(tmp_path, monkeypatch)
    store = prepared["store"]
    operation_id = "rename-lifecycle-catalog-drift"
    created, _applied = _source_applied(
        prepared, request, operation_id=operation_id
    )
    workspace = store.get_workspace("reviewer", request.workspace_id)
    store.quarantine_page_source_if_stale(
        "reviewer",
        request.workspace_id,
        request.source_owner_subject_id,
        request.source_page_id,
        source_revalidator=lambda _proof: False,
        expected_binding_version=request.expected_binding_version,
        base_version=workspace["version"],
        operation_id="quarantine-during-lifecycle",
    )
    source_calls = 0

    def must_not_revalidate(_receipt):
        nonlocal source_calls
        source_calls += 1
        return True

    with pytest.raises(WorkspacePageTreeSourceFenceConflict):
        store.finalize_page_lifecycle_command(
            "reviewer",
            request.workspace_id,
            created["command_id"],
            operation_id=operation_id,
            worker="worker.1",
            lease_token=LEASE_TOKEN,
            fencing_generation=1,
            expected_command_version=3,
            source_revalidator=must_not_revalidate,
        )
    assert source_calls == 0
    con = store._read_connection()
    try:
        assert con.execute(
            "SELECT status FROM workspace_page_lifecycle_commands WHERE id=?",
            (created["command_id"],),
        ).fetchone()[0] == "source_applied"
        assert con.execute(
            "SELECT COUNT(*) FROM workspace_operations WHERE operation_id=?",
            (operation_id,),
        ).fetchone()[0] == 0
    finally:
        con.close()


@pytest.mark.parametrize("phase", ["record", "finalize"])
def test_callback_lease_expiry_is_write_free(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    prepared, request = _materialized(tmp_path, monkeypatch)
    store = prepared["store"]
    clock = [int(time.time())]
    store._trusted_clock = lambda: clock[0]
    operation_id = f"rename-lifecycle-expiry-{phase}"
    created = store.prepare_page_lifecycle_command(
        "reviewer", request, operation_id=operation_id
    )
    store.claim_page_lifecycle_command(
        "reviewer",
        request.workspace_id,
        created["command_id"],
        operation_id=operation_id,
        worker="worker.1",
        lease_token=LEASE_TOKEN,
        lease_seconds=5,
        expected_command_version=1,
    )

    if phase == "record":
        def expire_on_record(_receipt):
            clock[0] += 5
            return True

        with pytest.raises(WorkspacePageLifecycleLeaseConflict):
            store.record_page_lifecycle_source_applied(
                "reviewer",
                request.workspace_id,
                created["command_id"],
                RECEIPT,
                operation_id=operation_id,
                worker="worker.1",
                lease_token=LEASE_TOKEN,
                fencing_generation=1,
                expected_command_version=2,
                source_revalidator=expire_on_record,
            )
        expected_status = "prepared"
        expected_version = 2
    else:
        store.record_page_lifecycle_source_applied(
            "reviewer",
            request.workspace_id,
            created["command_id"],
            RECEIPT,
            operation_id=operation_id,
            worker="worker.1",
            lease_token=LEASE_TOKEN,
            fencing_generation=1,
            expected_command_version=2,
            source_revalidator=lambda candidate: candidate == RECEIPT,
        )

        def expire_on_finalize(_receipt):
            clock[0] += 5
            return True

        with pytest.raises(WorkspacePageLifecycleLeaseConflict):
            store.finalize_page_lifecycle_command(
                "reviewer",
                request.workspace_id,
                created["command_id"],
                operation_id=operation_id,
                worker="worker.1",
                lease_token=LEASE_TOKEN,
                fencing_generation=1,
                expected_command_version=3,
                source_revalidator=expire_on_finalize,
            )
        expected_status = "source_applied"
        expected_version = 3

    con = store._read_connection()
    try:
        row = con.execute(
            "SELECT status,row_version FROM workspace_page_lifecycle_commands WHERE id=?",
            (created["command_id"],),
        ).fetchone()
        assert (row["status"], row["row_version"]) == (
            expected_status,
            expected_version,
        )
        assert con.execute(
            "SELECT COUNT(*) FROM workspace_operations WHERE operation_id=?",
            (operation_id,),
        ).fetchone()[0] == 0
    finally:
        con.close()


def test_corrupt_finalize_ledger_fails_replay_and_deep_health(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepared, request = _materialized(tmp_path, monkeypatch)
    store = prepared["store"]
    operation_id = "rename-lifecycle-corrupt-ledger"
    created, _applied = _source_applied(
        prepared, request, operation_id=operation_id
    )
    con = sqlite3.connect(store.db_path)
    try:
        namespace_sql = con.execute(
            "SELECT sql FROM sqlite_master WHERE type='trigger' "
            "AND name='trg_workspace_operation_lifecycle_namespace_insert'"
        ).fetchone()[0]
        con.execute("DROP TRIGGER trg_workspace_operation_lifecycle_namespace_insert")
        con.commit()
    finally:
        con.close()
    ready = store_module._schema_ready_on_connection
    monkeypatch.setattr(store_module, "_schema_ready_on_connection", lambda _con: True)
    try:
        store.finalize_page_lifecycle_command(
            "reviewer",
            request.workspace_id,
            created["command_id"],
            operation_id=operation_id,
            worker="worker.1",
            lease_token=LEASE_TOKEN,
            fencing_generation=1,
            expected_command_version=3,
            source_revalidator=lambda candidate: candidate == RECEIPT,
        )
    finally:
        monkeypatch.setattr(store_module, "_schema_ready_on_connection", ready)
    con = sqlite3.connect(store.db_path)
    try:
        con.execute(namespace_sql)
        trigger_sql = con.execute(
            "SELECT sql FROM sqlite_master WHERE type='trigger' "
            "AND name='trg_workspace_operations_immutable_update'"
        ).fetchone()[0]
        con.execute("DROP TRIGGER trg_workspace_operations_immutable_update")
        con.execute(
            "UPDATE workspace_operations SET result_json='{}' "
            "WHERE actor_subject_id='reviewer' AND operation_id=?",
            (operation_id,),
        )
        con.execute(trigger_sql)
        con.commit()
    finally:
        con.close()

    report = store.deep_health()
    assert report["healthy"] is False
    assert "workspace_page_lifecycle_commitment_invalid" in report["issues"]
    source_calls = 0

    def must_not_revalidate(_receipt):
        nonlocal source_calls
        source_calls += 1
        return True

    with pytest.raises(WorkspaceSubstrateUnavailable):
        store.finalize_page_lifecycle_command(
            "reviewer",
            request.workspace_id,
            created["command_id"],
            operation_id=operation_id,
            worker="worker.1",
            lease_token=LEASE_TOKEN,
            fencing_generation=1,
            expected_command_version=3,
            source_revalidator=must_not_revalidate,
        )
    with pytest.raises(WorkspaceSubstrateUnavailable):
        store.get_page_lifecycle_command_replay(
            "reviewer", request, operation_id=operation_id
        )
    with pytest.raises(WorkspaceSubstrateUnavailable):
        store.prepare_page_lifecycle_command(
            "reviewer", request, operation_id=operation_id
        )
    assert source_calls == 0

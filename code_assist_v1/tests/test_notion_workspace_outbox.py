from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from typing import Any

import pytest

from code_assist_v1.notion_workspace_outbox import (
    MAX_BATCH_LIMIT,
    PAGE_SOURCE_ACTIVATED_EVENT,
    PAGE_SOURCE_AGGREGATE,
    process_projection_outbox_batch,
    projection_effect_operation_id,
    retry_backoff_seconds,
)
from code_assist_v1.notion_workspace_projection import (
    LegacyProjectionAmbiguousError,
    LegacyProjectionPermissionError,
    LegacyProjectionStaleError,
    UnsafeLegacyFileError,
    project_exact_owner_page,
)
from code_assist_v1.notion_workspace_store import (
    WorkspaceRevisionConflict,
    WorkspaceStore,
    catalog_path_for_user_data_root,
)
from code_assist_v1 import notion_store
from code_assist_v1 import notion_workspace_projection as projection_module


def _delivery(index: int = 1, **overrides: Any) -> dict[str, Any]:
    owner = str(overrides.pop("owner", "owner_01"))
    page = str(overrides.pop("page", f"page-{index}"))
    workspace = str(overrides.pop("workspace", "ws_0123456789abcdef"))
    payload = overrides.pop(
        "payload",
        {
            "workspace_id": workspace,
            "source_owner_subject_id": owner,
            "source_page_id": page,
            "version": 3,
        },
    )
    result = {
        "delivery_id": f"delivery-{index}",
        "outbox_id": f"wout_{index}",
        "event_type": PAGE_SOURCE_ACTIVATED_EVENT,
        "aggregate_type": PAGE_SOURCE_AGGREGATE,
        "aggregate_id": f"{owner}:{page}",
        "workspace_id": workspace,
        "actor_subject_id": owner,
        "payload_json": json.dumps(payload),
        "created_at": 100,
        "status": "leased",
        "consumer": "np2a-page-locator-projection-v1",
        "attempt_count": 1,
        "available_at": 100,
        "lease_token": f"lease-{index}-1",
        "lease_expires_at": 160,
        "row_version": 1,
    }
    result.update(overrides)
    return result


class FakeStore:
    def __init__(self, deliveries: list[dict[str, Any]]) -> None:
        self.deliveries = [dict(item) for item in deliveries]
        self.claim_calls: list[dict[str, Any]] = []
        self.acks: list[str] = []
        self.retries: list[dict[str, Any]] = []
        self.dead_letters: list[dict[str, Any]] = []
        self.fail_next_ack = False

    def claim_pending_outbox(
        self,
        consumer: str,
        worker: str,
        *,
        limit: int,
        now: int | None,
        lease_seconds: int,
        max_attempts: int,
    ) -> list[dict[str, Any]]:
        self.claim_calls.append(
            {
                "consumer": consumer,
                "worker": worker,
                "limit": limit,
                "now": now,
                "lease_seconds": lease_seconds,
                "max_attempts": max_attempts,
            }
        )
        claimed: list[dict[str, Any]] = []
        for item in self.deliveries:
            if item.get("_state", "pending") != "pending" or len(claimed) >= limit:
                continue
            item["_state"] = "leased"
            item["lease_token"] = (
                f"lease-{item['outbox_id']}-{item['attempt_count']}"
            )
            claimed.append({key: value for key, value in item.items() if key != "_state"})
        return claimed

    def _by_lease(self, lease_token: str) -> dict[str, Any]:
        for item in self.deliveries:
            if item.get("lease_token") == lease_token and item.get("_state") == "leased":
                return item
        raise FileNotFoundError("outbox lease not found")

    def ack_outbox_delivery(
        self,
        consumer: str,
        worker: str,
        lease_token: str,
        *,
        now: int | None,
    ) -> dict[str, Any]:
        item = self._by_lease(lease_token)
        if self.fail_next_ack:
            self.fail_next_ack = False
            # Model expiry/reclaim after the process dies between effect/ack.
            item["_state"] = "pending"
            item["attempt_count"] += 1
            raise sqlite3.OperationalError("simulated ack interruption")
        item["_state"] = "delivered"
        self.acks.append(lease_token)
        return {"status": "delivered"}

    def retry_outbox_delivery(
        self,
        consumer: str,
        worker: str,
        lease_token: str,
        error_code: str,
        *,
        now: int | None,
        backoff_seconds: int,
        max_attempts: int,
    ) -> dict[str, Any]:
        item = self._by_lease(lease_token)
        record = {
            "error_code": error_code,
            "backoff_seconds": backoff_seconds,
            "max_attempts": max_attempts,
        }
        self.retries.append(record)
        if item["attempt_count"] >= max_attempts:
            item["_state"] = "dead_lettered"
            return {"status": "dead_lettered"}
        item["_state"] = "pending"
        item["attempt_count"] += 1
        return {"status": "retry_scheduled"}

    def dead_letter_outbox_delivery(
        self,
        consumer: str,
        worker: str,
        lease_token: str,
        error_code: str,
        *,
        now: int | None,
    ) -> dict[str, Any]:
        item = self._by_lease(lease_token)
        item["_state"] = "dead_lettered"
        self.dead_letters.append({"error_code": error_code})
        return {"status": "dead_lettered"}


def _run(
    store: FakeStore,
    projector: Any,
    **kwargs: Any,
) -> dict[str, Any]:
    return process_projection_outbox_batch(
        store,
        worker="offline-worker-01",
        user_data_root="X:/injected-user-data",
        expected_catalog_db_path="X:/injected-catalog/workspace.db",
        source_db_for_owner=lambda owner: f"X:/injected-user-data/{owner}/notion/notion.db",
        source_quiesced_confirmed=True,
        projector=projector,
        now=1_000,
        **kwargs,
    )


def test_success_forwards_exact_event_and_acks_with_secret_free_report() -> None:
    raw_secret = "public-bearer-secret-never-report"
    raw_path = "C:/private/customer/report.pdf"
    title = "private page title"
    delivery = _delivery(
        payload={
            "workspace_id": "ws_0123456789abcdef",
            "source_owner_subject_id": "owner_01",
            "source_page_id": "page-1",
            "version": 3,
            "title": title,
            "raw_token": raw_secret,
            "path": raw_path,
        }
    )
    store = FakeStore([delivery])
    calls: list[dict[str, Any]] = []

    def projector(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {"projection": {"changed": True}}

    report = _run(store, projector)

    assert report == {
        "claimed": 1,
        "projected": 1,
        "delivered": 1,
        "retried": 0,
        "dead_lettered": 0,
        "ignored": 0,
        "error_codes": {},
    }
    assert len(calls) == 1
    assert calls[0]["source_owner_id"] == "owner_01"
    assert calls[0]["actor_subject_id"] == "owner_01"
    assert calls[0]["source_page_id"] == "page-1"
    assert calls[0]["expected_binding_version"] == 3
    assert calls[0]["source_quiesced_confirmed"] is True
    serialized = json.dumps(report, sort_keys=True)
    assert raw_secret not in serialized
    assert raw_path not in serialized
    assert title not in serialized


def test_effect_then_ack_crash_converges_with_new_attempt_operation_id() -> None:
    store = FakeStore([_delivery()])
    store.fail_next_ack = True
    operation_ids: list[str] = []
    projected_state: set[tuple[str, str, str]] = set()
    actual_state_changes = 0

    def projector(**kwargs: Any) -> dict[str, Any]:
        nonlocal actual_state_changes
        operation_ids.append(kwargs["operation_id"])
        state = (
            kwargs["workspace_id"],
            kwargs["source_owner_id"],
            kwargs["source_page_id"],
        )
        changed = state not in projected_state
        if changed:
            projected_state.add(state)
            actual_state_changes += 1
        return {"projection": {"changed": changed}}

    with pytest.raises(sqlite3.OperationalError, match="ack interruption"):
        _run(store, projector)
    report = _run(store, projector)

    assert report["delivered"] == 1
    assert len(operation_ids) == 2
    assert operation_ids[0] != operation_ids[1]
    assert actual_state_changes == 1
    assert operation_ids == [
        projection_effect_operation_id(
            consumer="np2a-page-locator-projection-v1",
            outbox_id="wout_1",
            attempt_count=1,
        ),
        projection_effect_operation_id(
            consumer="np2a-page-locator-projection-v1",
            outbox_id="wout_1",
            attempt_count=2,
        ),
    ]


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (LegacyProjectionStaleError("private revision"), "source_revision_stale"),
        (WorkspaceRevisionConflict(99), "workspace_revision_conflict"),
        (
            ValueError("legacy page locator projection is invalid or stale"),
            "projection_cas_conflict",
        ),
        (sqlite3.OperationalError("database is locked at /secret/path"), "sqlite_transient"),
    ],
)
def test_retryable_failures_use_stable_codes_and_bounded_backoff(
    error: Exception,
    code: str,
) -> None:
    store = FakeStore([_delivery()])

    def projector(**kwargs: Any) -> dict[str, Any]:
        raise error

    report = _run(store, projector)

    assert report["retried"] == 1
    assert report["dead_lettered"] == 0
    assert report["error_codes"] == {code: 1}
    assert store.retries == [
        {"error_code": code, "backoff_seconds": 2, "max_attempts": 8}
    ]
    assert str(error) not in json.dumps(report, sort_keys=True)


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (LegacyProjectionPermissionError("private actor"), "source_permission_denied"),
        (UnsafeLegacyFileError("C:/secret/path"), "unsafe_source_path"),
        (LegacyProjectionAmbiguousError("secret token collision"), "ambiguous_source"),
        (PermissionError("private IAM detail"), "projection_permission_denied"),
        (ValueError("raw token was invalid"), "projection_input_invalid"),
    ],
)
def test_permanent_input_and_security_failures_dead_letter_without_details(
    error: Exception,
    code: str,
) -> None:
    store = FakeStore([_delivery()])

    def projector(**kwargs: Any) -> dict[str, Any]:
        raise error

    report = _run(store, projector)

    assert report["retried"] == 0
    assert report["dead_lettered"] == 1
    assert report["error_codes"] == {code: 1}
    assert store.dead_letters == [{"error_code": code}]
    assert str(error) not in json.dumps(report, sort_keys=True)


def test_wrong_event_is_never_projected_and_is_safely_ignored() -> None:
    delivery = _delivery(event_type="workspace.member_added")
    delivery["payload_json"] = json.dumps(
        {"title": "private title", "raw_token": "bearer-secret"}
    )
    store = FakeStore([delivery])
    calls: list[dict[str, Any]] = []

    report = _run(store, lambda **kwargs: calls.append(kwargs))

    assert calls == []
    assert report == {
        "claimed": 1,
        "projected": 0,
        "delivered": 0,
        "retried": 0,
        "dead_lettered": 1,
        "ignored": 1,
        "error_codes": {"event_not_allowlisted": 1},
    }
    assert store.dead_letters == [{"error_code": "event_not_allowlisted"}]
    assert "private title" not in json.dumps(report)
    assert "bearer-secret" not in json.dumps(report)


def test_batch_is_bounded_to_requested_limit() -> None:
    store = FakeStore([_delivery(index) for index in range(1, MAX_BATCH_LIMIT + 2)])
    calls: list[dict[str, Any]] = []

    report = _run(store, lambda **kwargs: calls.append(kwargs), limit=7)

    assert store.claim_calls[0]["limit"] == 7
    assert report["claimed"] == 7
    assert report["delivered"] == 7
    assert len(calls) == 7
    assert sum(item.get("_state", "pending") == "pending" for item in store.deliveries) == MAX_BATCH_LIMIT + 1 - 7


def test_quiescence_and_parameter_bounds_fail_before_claim() -> None:
    store = FakeStore([_delivery()])
    common = {
        "worker": "offline-worker-01",
        "user_data_root": "X:/root",
        "expected_catalog_db_path": "X:/catalog/workspace.db",
        "source_db_for_owner": lambda owner: f"X:/{owner}/notion.db",
        "projector": lambda **kwargs: {},
    }
    with pytest.raises(ValueError, match="explicitly true"):
        process_projection_outbox_batch(
            store, source_quiesced_confirmed=False, **common
        )
    with pytest.raises(ValueError, match="limit"):
        process_projection_outbox_batch(
            store,
            source_quiesced_confirmed=True,
            limit=MAX_BATCH_LIMIT + 1,
            **common,
        )
    with pytest.raises(ValueError, match="worker"):
        process_projection_outbox_batch(
            store,
            source_quiesced_confirmed=True,
            worker="Uppercase-Worker",
            user_data_root=common["user_data_root"],
            expected_catalog_db_path=common["expected_catalog_db_path"],
            source_db_for_owner=common["source_db_for_owner"],
            projector=common["projector"],
        )
    assert store.claim_calls == []


def test_invalid_envelope_is_dead_lettered_without_resolving_source() -> None:
    delivery = _delivery()
    delivery["payload_json"] = (
        '{"workspace_id":"ws_0123456789abcdef",'
        '"workspace_id":"ws_secret",'
        '"source_owner_subject_id":"owner_01",'
        '"source_page_id":"page-1","version":3}'
    )
    store = FakeStore([delivery])
    resolved: list[str] = []

    report = process_projection_outbox_batch(
        store,
        worker="offline-worker-01",
        user_data_root="X:/root",
        expected_catalog_db_path="X:/catalog/workspace.db",
        source_db_for_owner=lambda owner: resolved.append(owner) or "never",
        source_quiesced_confirmed=True,
        projector=lambda **kwargs: pytest.fail("must not project"),
    )

    assert resolved == []
    assert report["error_codes"] == {"invalid_event_payload": 1}
    assert report["dead_lettered"] == 1


def test_retry_schedule_is_deterministic_exponential_and_capped() -> None:
    assert retry_backoff_seconds(1) == 2
    assert retry_backoff_seconds(2) == 4
    assert retry_backoff_seconds(8) == 256
    assert retry_backoff_seconds(16) == 300


def test_worker_import_is_config_free_and_has_no_filesystem_side_effect(
    tmp_path: Path,
) -> None:
    probe = tmp_path / "import-probe"
    probe.mkdir()
    before = tuple(probe.iterdir())
    environment = os.environ.copy()
    environment["CODE_ASSIST_DATA_ROOT"] = str(probe)
    environment["CODE_ASSIST_TEST_DATA_ROOT"] = str(probe)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    script = (
        "import json,sys; import code_assist_v1.notion_workspace_outbox; "
        "print(json.dumps({'auth': 'code_assist_v1.auth' in sys.modules, "
        "'config': 'code_assist_v1.config' in sys.modules, "
        "'app': 'code_assist_v1.app_code' in sys.modules}))"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[2],
        env=environment,
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == {
        "auth": False,
        "config": False,
        "app": False,
    }
    assert tuple(probe.iterdir()) == before


def _actual_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(notion_store, "USER_DATA_DIR", str(tmp_path))
    store = WorkspaceStore(catalog_path_for_user_data_root(tmp_path))
    workspace = store.create_workspace(
        "owner", name="Outbox projection", operation_id="create-outbox-projection"
    )["workspace"]
    page = notion_store.create_page("owner", title="OUTBOX PRIVATE TITLE")["page"]
    notion_store.upload_file(
        "owner", page["id"], filename="outbox-secret.txt", raw=b"classified"
    )
    notion_store.enable_public_link("owner", page["id"])
    proof = notion_store.capture_legacy_page_source_proof(
        "owner", "owner", page["id"]
    )
    bound = store.bind_page_source(
        "owner",
        workspace["id"],
        proof,
        source_revalidator=notion_store.revalidate_legacy_page_source_proof,
        base_version=workspace["version"],
        operation_id="bind-outbox-projection",
    )
    return {
        "store": store,
        "workspace": workspace,
        "page": page,
        "bound": bound,
        "source": tmp_path / "owner" / "notion" / "notion.db",
        "catalog": Path(store.db_path),
    }


def _advance_actual_binding_generation(fixture: dict[str, Any]) -> dict[str, Any]:
    proof = notion_store.capture_legacy_page_source_proof(
        "owner", "owner", fixture["page"]["id"]
    )
    staged = fixture["store"].stage_page_source_binding(
        "owner",
        fixture["workspace"]["id"],
        proof,
        base_version=fixture["bound"]["workspace"]["version"],
        operation_id="restage-outbox-projection-v2",
    )
    return fixture["store"].activate_page_source_binding(
        "owner",
        fixture["workspace"]["id"],
        proof,
        source_revalidator=notion_store.revalidate_legacy_page_source_proof,
        expected_binding_version=staged["binding"]["version"],
        base_version=staged["workspace"]["version"],
        operation_id="activate-outbox-projection-v2",
    )


def _run_actual(
    fixture: dict[str, Any],
    *,
    store: Any | None = None,
    projector: Any = project_exact_owner_page,
    now: int,
) -> dict[str, Any]:
    return process_projection_outbox_batch(
        store or fixture["store"],
        worker="offline-worker-01",
        user_data_root=fixture["catalog"].parents[1],
        expected_catalog_db_path=fixture["catalog"],
        source_db_for_owner=lambda owner: fixture["source"],
        source_quiesced_confirmed=True,
        projector=projector,
        now=now,
        lease_seconds=1,
    )


def test_actual_store_claim_project_ack_in_temporary_exact_owner_catalog(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _actual_fixture(tmp_path, monkeypatch)

    report = _run_actual(fixture, now=1_000)

    assert report == {
        "claimed": 1,
        "projected": 1,
        "delivered": 1,
        "retried": 0,
        "dead_lettered": 0,
        "ignored": 0,
        "error_codes": {},
    }
    con = sqlite3.connect(fixture["catalog"])
    try:
        delivery = con.execute(
            """SELECT status,attempt_count,last_error_code
               FROM workspace_outbox_deliveries"""
        ).fetchone()
        projection = con.execute(
            """SELECT file_source_count,public_source_count
               FROM workspace_page_source_projections"""
        ).fetchone()
    finally:
        con.close()
    assert delivery == ("delivered", 1, None)
    assert projection == (1, 1)


def test_stale_activation_event_generation_dead_letters_before_legacy_locator_capture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _actual_fixture(tmp_path, monkeypatch)
    bound_v1 = fixture["bound"]["binding"]["version"]
    active_v2 = _advance_actual_binding_generation(fixture)
    assert active_v2["binding"]["version"] == bound_v1 + 1

    delivery = _delivery(
        workspace=fixture["workspace"]["id"],
        owner="owner",
        page=fixture["page"]["id"],
        payload={
            "workspace_id": fixture["workspace"]["id"],
            "source_owner_subject_id": "owner",
            "source_page_id": fixture["page"]["id"],
            "version": bound_v1,
        },
    )
    delivery_store = FakeStore([delivery])
    opened: list[Path] = []
    original_open = projection_module._open_read_only

    def tracked_open(path):
        opened.append(Path(path).resolve())
        return original_open(path)

    with monkeypatch.context() as scoped:
        scoped.setattr(projection_module, "_open_read_only", tracked_open)
        scoped.setattr(
            WorkspaceStore,
            "replace_page_locator_projection",
            lambda *args, **kwargs: pytest.fail("store replace must not run"),
        )
        report = process_projection_outbox_batch(
            delivery_store,
            worker="offline-worker-01",
            user_data_root=tmp_path,
            expected_catalog_db_path=fixture["catalog"],
            source_db_for_owner=lambda owner: fixture["source"],
            source_quiesced_confirmed=True,
            projector=project_exact_owner_page,
            now=1_500,
        )

    assert report == {
        "claimed": 1,
        "projected": 0,
        "delivered": 0,
        "retried": 0,
        "dead_lettered": 1,
        "ignored": 0,
        "error_codes": {"binding_generation_superseded": 1},
    }
    assert delivery_store.retries == []
    assert delivery_store.dead_letters == [
        {"error_code": "binding_generation_superseded"}
    ]
    assert delivery_store.deliveries[0]["_state"] == "dead_lettered"
    assert delivery_store.deliveries[0]["attempt_count"] == 1
    assert opened == [fixture["catalog"].resolve()]
    con = sqlite3.connect(fixture["catalog"])
    try:
        assert con.execute(
            "SELECT COUNT(*) FROM workspace_page_source_projections"
        ).fetchone()[0] == 0
    finally:
        con.close()


class _CrashAckStore:
    def __init__(self, delegate: WorkspaceStore) -> None:
        self.delegate = delegate
        self.failed = False

    def __getattr__(self, name: str) -> Any:
        return getattr(self.delegate, name)

    def ack_outbox_delivery(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        if not self.failed:
            self.failed = True
            raise sqlite3.OperationalError("simulated process loss before ack")
        return self.delegate.ack_outbox_delivery(*args, **kwargs)


def test_actual_store_effect_ack_crash_reclaims_and_semantically_converges(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _actual_fixture(tmp_path, monkeypatch)
    crash_store = _CrashAckStore(fixture["store"])
    effect_results: list[dict[str, Any]] = []

    def recording_projector(**kwargs: Any) -> dict[str, Any]:
        result = project_exact_owner_page(**kwargs)
        effect_results.append(result)
        return result

    with pytest.raises(sqlite3.OperationalError, match="process loss before ack"):
        _run_actual(
            fixture,
            store=crash_store,
            projector=recording_projector,
            now=2_000,
        )
    report = _run_actual(
        fixture,
        store=fixture["store"],
        projector=recording_projector,
        now=2_002,
    )

    assert report["delivered"] == 1
    assert [result["projection"]["changed"] for result in effect_results] == [
        True,
        False,
    ]
    assert effect_results[0]["workspace"]["version"] == effect_results[1]["workspace"]["version"]
    con = sqlite3.connect(fixture["catalog"])
    try:
        delivery = con.execute(
            "SELECT status,attempt_count FROM workspace_outbox_deliveries"
        ).fetchone()
        projection_count = con.execute(
            "SELECT COUNT(*) FROM workspace_page_source_projections"
        ).fetchone()[0]
        operations = con.execute(
            """SELECT operation_id FROM workspace_operations
               WHERE action='workspace.page_locator_projection_replaced'
               ORDER BY created_at,operation_id"""
        ).fetchall()
    finally:
        con.close()
    assert delivery == ("delivered", 2)
    assert projection_count == 1
    assert len(operations) == 2
    assert operations[0][0] != operations[1][0]


def test_actual_store_never_claims_nonallowlisted_parent_events(
    tmp_path: Path,
) -> None:
    store = WorkspaceStore(catalog_path_for_user_data_root(tmp_path))
    store.create_workspace(
        "owner", name="Only unrelated events", operation_id="create-unrelated"
    )
    projector_calls: list[dict[str, Any]] = []

    report = process_projection_outbox_batch(
        store,
        worker="offline-worker-01",
        user_data_root=tmp_path,
        expected_catalog_db_path=Path(store.db_path),
        source_db_for_owner=lambda owner: pytest.fail("must not resolve owner"),
        source_quiesced_confirmed=True,
        projector=lambda **kwargs: projector_calls.append(kwargs),
        now=3_000,
    )

    assert report["claimed"] == 0
    assert report["delivered"] == 0
    assert projector_calls == []
    con = sqlite3.connect(store.db_path)
    try:
        assert con.execute(
            "SELECT COUNT(*) FROM workspace_outbox_deliveries"
        ).fetchone()[0] == 0
        assert con.execute(
            "SELECT COUNT(*) FROM workspace_outbox WHERE delivered_at IS NULL"
        ).fetchone()[0] == 1
    finally:
        con.close()

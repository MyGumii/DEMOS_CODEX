from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from code_assist_v1.notion_workspace_page_lifecycle import (
    WorkspacePageLifecycleSourceEffectFailed,
    WorkspacePageLifecycleSourceOutcomeAmbiguous,
    run_disabled_page_lifecycle_command,
)
from code_assist_v1.tests.test_notion_workspace_page_lifecycle_kernel import (
    LEASE_TOKEN,
    RECEIPT,
    _materialized,
)


class _TempSourceEffect:
    def __init__(self, path: Path, *, lose_first_response: bool = False):
        self.path = path
        self.apply_calls = 0
        self.revalidation_calls = 0
        self.lose_first_response = lose_first_response
        self.response_lost = False
        con = sqlite3.connect(path)
        try:
            con.execute(
                """
                CREATE TABLE effects(
                  operation_id TEXT PRIMARY KEY,
                  request_sha256 TEXT NOT NULL,
                  receipt_sha256 TEXT NOT NULL
                )
                """
            )
            con.commit()
        finally:
            con.close()

    def apply(self, request, _command_id: str, operation_id: str):
        self.apply_calls += 1
        request_sha256 = "sha256:" + hashlib.sha256(
            json.dumps(
                request.descriptor(),
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        con = sqlite3.connect(self.path)
        try:
            previous = con.execute(
                "SELECT request_sha256,receipt_sha256 FROM effects WHERE operation_id=?",
                (operation_id,),
            ).fetchone()
            if previous is None:
                con.execute(
                    "INSERT INTO effects VALUES(?,?,?)",
                    (operation_id, request_sha256, RECEIPT.source_receipt_sha256),
                )
                con.commit()
            elif previous != (request_sha256, RECEIPT.source_receipt_sha256):
                raise AssertionError("source operation replay mismatch")
        finally:
            con.close()
        if self.lose_first_response and not self.response_lost:
            self.response_lost = True
            raise RuntimeError("simulated source response loss")
        return RECEIPT

    def revalidate(self, receipt):
        self.revalidation_calls += 1
        con = sqlite3.connect(self.path)
        try:
            row = con.execute(
                "SELECT COUNT(*) FROM effects WHERE receipt_sha256=?",
                (receipt.source_receipt_sha256,),
            ).fetchone()
        finally:
            con.close()
        return receipt == RECEIPT and row[0] == 1

    def effect_count(self) -> int:
        con = sqlite3.connect(self.path)
        try:
            return int(con.execute("SELECT COUNT(*) FROM effects").fetchone()[0])
        finally:
            con.close()


def _run(
    store,
    request,
    source: _TempSourceEffect,
    *,
    operation_id: str,
    source_quiesced_confirmed: bool = True,
):
    return run_disabled_page_lifecycle_command(
        store,
        request,
        actor_subject_id="reviewer",
        operation_id=operation_id,
        worker="worker.1",
        lease_token=LEASE_TOKEN,
        lease_seconds=120,
        source_quiesced_confirmed=source_quiesced_confirmed,
        source_effect=source.apply,
        source_revalidator=source.revalidate,
    )


def test_legacy_v1_kernel_is_disabled_before_source_or_catalog_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepared, request = _materialized(tmp_path, monkeypatch)
    source = _TempSourceEffect(tmp_path / "fake-source.db")
    with pytest.raises(
        RuntimeError, match="legacy page lifecycle v1 kernel is disabled"
    ):
        _run(
            prepared["store"],
            request,
            source,
            operation_id="disabled-rename-v1",
        )
    assert source.apply_calls == 0
    assert source.revalidation_calls == 0
    assert source.effect_count() == 0
    con = prepared["store"]._read_connection()
    try:
        assert con.execute(
            "SELECT COUNT(*) FROM workspace_page_lifecycle_commands "
            "WHERE operation_id='disabled-rename-v1'"
        ).fetchone()[0] == 0
    finally:
        con.close()


def test_catalog_finalize_fault_retries_without_reapplying_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepared, request = _materialized(tmp_path, monkeypatch)
    store = prepared["store"]
    source = _TempSourceEffect(tmp_path / "fake-source.db")
    operation_id = "disabled-rename-finalize-crash"
    write_calls = 0

    def forbidden_write_connection():
        nonlocal write_calls
        write_calls += 1
        raise AssertionError("catalog writer opened by disabled v1 kernel")

    monkeypatch.setattr(store, "_write_connection", forbidden_write_connection)
    with pytest.raises(
        RuntimeError, match="legacy page lifecycle v1 kernel is disabled"
    ):
        _run(store, request, source, operation_id=operation_id)
    assert write_calls == 0
    assert source.apply_calls == 0
    assert source.effect_count() == 0


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (WorkspacePageLifecycleSourceEffectFailed, "failed"),
        (WorkspacePageLifecycleSourceOutcomeAmbiguous, "manual_audit"),
    ],
)
def test_source_terminal_outcome_is_durable_and_replays_without_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error,
    status: str,
) -> None:
    prepared, request = _materialized(tmp_path, monkeypatch)
    calls = 0

    def source_effect(_request, _command_id, _operation_id):
        nonlocal calls
        calls += 1
        raise error("injected terminal source outcome")

    operation_id = f"disabled-rename-{status}"
    with pytest.raises(
        RuntimeError, match="legacy page lifecycle v1 kernel is disabled"
    ):
        run_disabled_page_lifecycle_command(
            prepared["store"],
            request,
            actor_subject_id="reviewer",
            operation_id=operation_id,
            worker="worker.1",
            lease_token=LEASE_TOKEN,
            lease_seconds=120,
            source_quiesced_confirmed=True,
            source_effect=source_effect,
            source_revalidator=lambda _receipt: True,
        )
    assert calls == 0


def test_page_lifecycle_orchestration_import_is_side_effect_free(
    tmp_path: Path,
) -> None:
    probe = tmp_path / "import-probe"
    probe.mkdir()
    before = tuple(probe.iterdir())
    environment = os.environ.copy()
    environment["CODE_ASSIST_DATA_ROOT"] = str(probe)
    environment["CODE_ASSIST_TEST_DATA_ROOT"] = str(probe)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    forbidden = (
        "code_assist_v1.app_code",
        "code_assist_v1.app_notion",
        "code_assist_v1.routes_notion_bridge",
        "code_assist_v1.routes_notion_workspace",
        "code_assist_v1.routes_knowledge",
        "code_assist_v1.routes_second_brain",
        "code_assist_v1.second_brain_store",
        "code_assist_v1.knowledge_core",
    )
    script = (
        "import json,sys; "
        "import code_assist_v1.notion_workspace_page_lifecycle; "
        f"names={forbidden!r}; "
        "print(json.dumps(sorted(name for name in names if name in sys.modules)))"
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
    assert json.loads(completed.stdout) == []
    assert tuple(probe.iterdir()) == before


def test_new_command_requires_quiescence_before_catalog_or_source_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepared, request = _materialized(tmp_path, monkeypatch)
    source_calls = 0

    def source_effect(*_args):
        nonlocal source_calls
        source_calls += 1
        return RECEIPT

    with pytest.raises(ValueError, match="requires source quiescence"):
        run_disabled_page_lifecycle_command(
            prepared["store"],
            request,
            actor_subject_id="reviewer",
            operation_id="disabled-rename-no-quiescence",
            worker="worker.1",
            lease_token=LEASE_TOKEN,
            lease_seconds=120,
            source_quiesced_confirmed=False,
            source_effect=source_effect,
            source_revalidator=lambda _receipt: True,
        )
    assert source_calls == 0
    con = prepared["store"]._read_connection()
    try:
        assert con.execute(
            "SELECT COUNT(*) FROM workspace_page_lifecycle_commands "
            "WHERE operation_id='disabled-rename-no-quiescence'"
        ).fetchone()[0] == 0
    finally:
        con.close()

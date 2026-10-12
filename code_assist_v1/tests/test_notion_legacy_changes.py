from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sqlite3
import sys

import pytest

from code_assist_v1 import notion_store
from code_assist_v1.notion_legacy_change_migration import apply_plan, build_plan
from code_assist_v1 import notion_legacy_changes as legacy_changes
from code_assist_v1.notion_legacy_changes import (
    LegacyChangeReadError,
    LegacyPageChangeReceipt,
    LegacySourceFence,
    read_legacy_page_change_receipt,
    scan_legacy_page_changes,
)


def _fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = tmp_path / "user_data"
    root.mkdir()
    monkeypatch.setattr(notion_store, "USER_DATA_DIR", str(root))
    pages = [
        notion_store.create_page("owner", title=f"PRIVATE CHANGE TITLE {index}")["page"]
        for index in range(3)
    ]
    source = root / "owner" / "notion" / "notion.db"
    backups = root / "backups"
    backups.mkdir()
    plan = build_plan(
        user_data_root=root,
        source_owner_id="owner",
        expected_source_db_path=source,
    )
    apply_plan(
        plan,
        confirm_checksum=plan["plan_checksum"],
        source_quiesced_confirmed=True,
        backup_confirmed=True,
        expected_backup_path=backups / "before.db",
    )
    return root, source, pages


def test_change_scan_is_bounded_ordered_resumable_and_secret_free(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, source, pages = _fixture(tmp_path, monkeypatch)
    first = scan_legacy_page_changes(
        user_data_root=root,
        source_owner_id="owner",
        expected_source_db_path=source,
        source_quiesced_confirmed=True,
        limit=2,
    )
    assert len(first.items) == first.scanned_count == 2
    assert first.has_more is True
    assert first.next_cursor is not None
    second = scan_legacy_page_changes(
        user_data_root=root,
        source_owner_id="owner",
        expected_source_db_path=source,
        source_quiesced_confirmed=True,
        after=first.next_cursor,
        limit=2,
    )
    assert len(second.items) == second.scanned_count == 1
    assert second.has_more is False
    assert second.next_cursor is None
    observed = [item.page_id for item in (*first.items, *second.items)]
    assert observed == sorted(page["id"] for page in pages)
    text = json.dumps(
        [item.__dict__ for item in (*first.items, *second.items)], sort_keys=True
    )
    assert "PRIVATE CHANGE TITLE" not in text


def test_quiescence_and_limit_fail_before_path_access(tmp_path: Path) -> None:
    missing = tmp_path / "missing.db"
    with pytest.raises(LegacyChangeReadError, match="quiescence"):
        scan_legacy_page_changes(
            user_data_root=tmp_path / "missing-root",
            source_owner_id="owner",
            expected_source_db_path=missing,
            source_quiesced_confirmed=False,
        )
    with pytest.raises(LegacyChangeReadError, match="limit"):
        scan_legacy_page_changes(
            user_data_root=tmp_path / "missing-root",
            source_owner_id="owner",
            expected_source_db_path=missing,
            source_quiesced_confirmed=True,
            limit=33,
        )
    assert not missing.exists()


def test_exact_receipt_is_fenced_secret_free_and_quiescence_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, source, pages = _fixture(tmp_path, monkeypatch)
    receipt = read_legacy_page_change_receipt(
        user_data_root=root,
        source_owner_id="owner",
        page_id=pages[0]["id"],
        expected_source_db_path=source,
        source_quiesced_confirmed=True,
    )
    assert isinstance(receipt, LegacyPageChangeReceipt)
    assert isinstance(receipt.fence, LegacySourceFence)
    assert receipt.page_id == pages[0]["id"]
    assert receipt.generation == 1
    assert receipt.mask > 0
    assert receipt.source_exists is True
    assert receipt.source_active is True
    assert receipt.fence.source_epoch >= 1
    assert receipt.fence.full_sweep_generation >= 1
    assert receipt.fence.link_count == 1
    assert "PRIVATE CHANGE TITLE" not in json.dumps(
        receipt, default=lambda value: value.__dict__, sort_keys=True
    )

    with monkeypatch.context() as scoped:
        scoped.setattr(
            legacy_changes,
            "resolve_legacy_change_paths",
            lambda **_kwargs: pytest.fail("paths must not be consulted"),
        )
        with pytest.raises(LegacyChangeReadError, match="quiescence"):
            read_legacy_page_change_receipt(
                user_data_root=tmp_path / "missing-root",
                source_owner_id="owner",
                page_id=pages[0]["id"],
                expected_source_db_path=tmp_path / "missing.db",
                source_quiesced_confirmed=False,
            )


def test_exact_receipt_rejects_watermark_page_existence_disagreement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, source, pages = _fixture(tmp_path, monkeypatch)
    con = sqlite3.connect(source)
    try:
        con.execute(
            "UPDATE notion_page_change_watermarks SET source_exists=0 WHERE page_id=?",
            (pages[0]["id"],),
        )
        con.commit()
    finally:
        con.close()
    with pytest.raises(LegacyChangeReadError, match="tracking is invalid"):
        read_legacy_page_change_receipt(
            user_data_root=root,
            source_owner_id="owner",
            page_id=pages[0]["id"],
            expected_source_db_path=source,
            source_quiesced_confirmed=True,
        )


def test_exact_receipt_distinguishes_archived_row_from_deleted_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, source, pages = _fixture(tmp_path, monkeypatch)
    page_id = pages[0]["id"]
    notion_store.archive_page("owner", page_id)
    archived = read_legacy_page_change_receipt(
        user_data_root=root,
        source_owner_id="owner",
        page_id=page_id,
        expected_source_db_path=source,
        source_quiesced_confirmed=True,
    )
    assert archived.source_exists is True
    assert archived.source_active is False

    notion_store.delete_page_permanent("owner", page_id)
    deleted = read_legacy_page_change_receipt(
        user_data_root=root,
        source_owner_id="owner",
        page_id=page_id,
        expected_source_db_path=source,
        source_quiesced_confirmed=True,
    )
    assert deleted.source_exists is False
    assert deleted.source_active is False
    assert deleted.generation > archived.generation


def test_exact_receipt_rejects_page_from_a_different_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, source, pages = _fixture(tmp_path, monkeypatch)
    con = sqlite3.connect(source)
    try:
        con.execute(
            "UPDATE pages SET owner_user_id='other' WHERE id=?", (pages[0]["id"],)
        )
        con.commit()
    finally:
        con.close()
    with pytest.raises(LegacyChangeReadError, match="tracking is invalid"):
        read_legacy_page_change_receipt(
            user_data_root=root,
            source_owner_id="owner",
            page_id=pages[0]["id"],
            expected_source_db_path=source,
            source_quiesced_confirmed=True,
        )


def test_unmigrated_source_fails_closed_without_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "user_data"
    root.mkdir()
    monkeypatch.setattr(notion_store, "USER_DATA_DIR", str(root))
    notion_store.create_page("owner", title="unmigrated")
    source = root / "owner" / "notion" / "notion.db"
    before = (source.stat().st_size, source.stat().st_mtime_ns)
    with pytest.raises(LegacyChangeReadError, match="unavailable"):
        scan_legacy_page_changes(
            user_data_root=root,
            source_owner_id="owner",
            expected_source_db_path=source,
            source_quiesced_confirmed=True,
        )
    assert (source.stat().st_size, source.stat().st_mtime_ns) == before


def test_corrupt_watermark_value_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, source, pages = _fixture(tmp_path, monkeypatch)
    con = sqlite3.connect(source)
    try:
        con.execute("PRAGMA ignore_check_constraints=ON")
        con.execute(
            "UPDATE notion_page_change_watermarks SET change_generation='secret-path' "
            "WHERE page_id=?",
            (pages[0]["id"],),
        )
        con.commit()
    finally:
        con.close()
    with pytest.raises(LegacyChangeReadError, match="tracking is invalid") as caught:
        scan_legacy_page_changes(
            user_data_root=root,
            source_owner_id="owner",
            expected_source_db_path=source,
            source_quiesced_confirmed=True,
        )
    assert "secret-path" not in str(caught.value)


def test_resume_rejects_source_drift_and_skips_repeated_integrity_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, source, pages = _fixture(tmp_path, monkeypatch)
    integrity_flags: list[bool] = []
    original_inspect = legacy_changes._inspect

    def inspect_spy(con, *, verify_integrity=True):
        integrity_flags.append(bool(verify_integrity))
        return original_inspect(con, verify_integrity=verify_integrity)

    monkeypatch.setattr(legacy_changes, "_inspect", inspect_spy)
    first = scan_legacy_page_changes(
        user_data_root=root,
        source_owner_id="owner",
        expected_source_db_path=source,
        source_quiesced_confirmed=True,
        limit=1,
    )
    assert first.next_cursor is not None
    second = scan_legacy_page_changes(
        user_data_root=root,
        source_owner_id="owner",
        expected_source_db_path=source,
        source_quiesced_confirmed=True,
        after=first.next_cursor,
        limit=1,
    )
    assert second.items
    assert integrity_flags == [True, False]

    notion_store.update_page("owner", pages[-1]["id"], {"title": "changed"})
    with pytest.raises(LegacyChangeReadError, match="source changed"):
        scan_legacy_page_changes(
            user_data_root=root,
            source_owner_id="owner",
            expected_source_db_path=source,
            source_quiesced_confirmed=True,
            after=first.next_cursor,
            limit=1,
        )


def test_change_reader_import_does_not_load_apps_or_second_brain(tmp_path: Path) -> None:
    probe = tmp_path / "probe"
    probe.mkdir()
    forbidden = (
        "code_assist_v1.app_code",
        "code_assist_v1.app_notion",
        "code_assist_v1.routes_notion_bridge",
        "code_assist_v1.second_brain_store",
        "code_assist_v1.knowledge_core",
        "code_assist_v1.notion_legacy_change_migration",
    )
    script = (
        "import json,sys; import code_assist_v1.notion_legacy_changes; "
        f"x={forbidden!r}; print(json.dumps([n for n in x if n in sys.modules]))"
    )
    environment = os.environ.copy()
    environment["CODE_ASSIST_DATA_ROOT"] = str(probe)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    before = tuple(probe.iterdir())
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[2],
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == []
    assert tuple(probe.iterdir()) == before

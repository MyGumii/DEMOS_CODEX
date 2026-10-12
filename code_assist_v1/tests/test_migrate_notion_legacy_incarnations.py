from __future__ import annotations

import json
from pathlib import Path
import sqlite3

import pytest

from code_assist_v1 import notion_store
from code_assist_v1 import notion_legacy_change_migration as v1_migration
from code_assist_v1 import notion_legacy_incarnation_migration as v2_migration
from code_assist_v1.notion_legacy_change_contract import (
    EXPECTED_OBJECT_HASH as V1_OBJECT_HASH,
)
from code_assist_v1.notion_legacy_incarnation_contract import (
    EXPECTED_OBJECT_HASH,
    MIGRATION_CHECKSUM,
    inspect_legacy_incarnation_contract,
)
from code_assist_v1.notion_legacy_changes import (
    LegacyChangeReadError,
    legacy_incarnation_receipt_sha256,
    read_legacy_page_incarnation_receipt_v2,
    sqlite_source_identity,
)


def _apply_v1(root: Path, source: Path, backups: Path) -> None:
    plan = v1_migration.build_plan(
        user_data_root=root,
        source_owner_id="owner",
        expected_source_db_path=source,
    )
    v1_migration.apply_plan(
        plan,
        confirm_checksum=plan["plan_checksum"],
        source_quiesced_confirmed=True,
        backup_confirmed=True,
        expected_backup_path=backups / "before-v1.db",
    )


def _apply_v2(root: Path, source: Path, backups: Path) -> dict[str, object]:
    plan = v2_migration.build_plan(
        user_data_root=root,
        source_owner_id="owner",
        expected_source_db_path=source,
    )
    result = v2_migration.apply_plan(
        plan,
        confirm_checksum=plan["plan_checksum"],
        source_quiesced_confirmed=True,
        backup_confirmed=True,
        expected_backup_path=backups / "before-v2.db",
    )
    assert result["status"] == "applied"
    return plan


@pytest.fixture
def migrated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = tmp_path / "user_data"
    root.mkdir()
    backups = root / "backups"
    backups.mkdir()
    monkeypatch.setattr(notion_store, "USER_DATA_DIR", str(root))
    live = notion_store.create_page("owner", title="SECRET LIVE")["page"]
    archived = notion_store.create_page("owner", title="SECRET ARCHIVED")["page"]
    deleted = notion_store.create_page("owner", title="SECRET DELETED")["page"]
    source = root / "owner" / "notion" / "notion.db"
    _apply_v1(root, source, backups)
    notion_store.archive_page("owner", archived["id"])
    notion_store.archive_page("owner", deleted["id"])
    notion_store.delete_page_permanent("owner", deleted["id"])
    _apply_v2(root, source, backups)
    return root, source, backups, live, archived, deleted


def _history(source: Path, page_id: str) -> list[tuple[object, ...]]:
    con = sqlite3.connect(source)
    try:
        return con.execute(
            """
            SELECT incarnation_generation,incarnation_id,retired_at
            FROM notion_page_incarnation_history
            WHERE page_id=? ORDER BY incarnation_generation
            """,
            (page_id,),
        ).fetchall()
    finally:
        con.close()


def test_v2_contract_fingerprints_are_frozen_and_v1_is_unchanged() -> None:
    assert MIGRATION_CHECKSUM == (
        "sha256:db029078d58d1220fb382a29840cf236149c5191b1c0c83fb26291fe1d8e7903"
    )
    assert EXPECTED_OBJECT_HASH == (
        "sha256:207c56ae66ae10d0cd0e32753993572df0cbe4add779cb9f93379a459b73f42d"
    )
    assert v1_migration._EXPECTED_OBJECT_HASH == V1_OBJECT_HASH == (
        "sha256:020c9cd1288120668e084d97f4e6304a7c9332ddb468436973496a4af301590e"
    )


def test_migration_requires_exact_v1_and_is_deterministic_read_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "user_data"
    root.mkdir()
    backups = root / "backups"
    backups.mkdir()
    monkeypatch.setattr(notion_store, "USER_DATA_DIR", str(root))
    notion_store.create_page("owner", title="private")
    source = root / "owner" / "notion" / "notion.db"
    with pytest.raises(v2_migration.LegacyIncarnationMigrationRefused):
        v2_migration.build_plan(
            user_data_root=root,
            source_owner_id="owner",
            expected_source_db_path=source,
        )
    _apply_v1(root, source, backups)
    before = source.read_bytes()
    first = v2_migration.build_plan(
        user_data_root=root, source_owner_id="owner",
        expected_source_db_path=source,
    )
    second = v2_migration.build_plan(
        user_data_root=root, source_owner_id="owner",
        expected_source_db_path=source,
    )
    assert first == second
    assert first["action"] == "migrate_v1_to_v2"
    assert source.read_bytes() == before


def test_backfill_distinguishes_archived_physical_page_and_deleted_tombstone(
    migrated: tuple[Path, Path, Path, dict, dict, dict]
) -> None:
    _root, source, _backups, live, archived, deleted = migrated
    live_row = _history(source, live["id"])
    archived_row = _history(source, archived["id"])
    deleted_row = _history(source, deleted["id"])
    assert live_row[0][0] == archived_row[0][0] == deleted_row[0][0] == 1
    assert live_row[0][2] is None
    assert archived_row[0][2] is None
    assert deleted_row[0][2] is not None
    assert all(str(rows[0][1]).startswith("inc_") for rows in (
        live_row, archived_row, deleted_row
    ))


def test_ordinary_store_create_allocates_nonce_and_recreate_advances_generation(
    migrated: tuple[Path, Path, Path, dict, dict, dict]
) -> None:
    _root, source, _backups, _live, _archived, deleted = migrated
    page_id = deleted["id"]
    con = notion_store._connect_write("owner")
    try:
        now = 1_800_000_000
        con.execute(
            """
            INSERT INTO pages(id,owner_user_id,title,created_at,updated_at)
            VALUES(?,?,?,?,?)
            """,
            (page_id, "owner", "new physical page", now, now),
        )
        con.commit()
    finally:
        con.close()
    rows = _history(source, page_id)
    assert [row[0] for row in rows] == [1, 2]
    assert rows[0][2] is not None and rows[1][2] is None
    assert rows[0][1] != rows[1][1]


def test_raw_sqlite_page_lifecycle_without_udfs_fails_atomically(
    migrated: tuple[Path, Path, Path, dict, dict, dict]
) -> None:
    _root, source, _backups, live, _archived, _deleted = migrated
    before = _history(source, live["id"])
    statements = (
        (
            "INSERT INTO pages(id,owner_user_id,title,created_at,updated_at) "
            "VALUES('raw-page','owner','raw',1,1)",
            (),
        ),
        ("DELETE FROM pages WHERE id=?", (live["id"],)),
        ("UPDATE pages SET id='raw-renamed' WHERE id=?", (live["id"],)),
        (
            "INSERT OR REPLACE INTO pages(id,owner_user_id,title,created_at,updated_at) "
            "VALUES(?,?,?,?,?)",
            (live["id"], "owner", "replace", 1, 1),
        ),
    )
    for sql, params in statements:
        con = sqlite3.connect(source)
        try:
            with pytest.raises(sqlite3.Error):
                con.execute(sql, params)
                con.commit()
            con.rollback()
        finally:
            con.close()
        assert _history(source, live["id"]) == before
    con = sqlite3.connect(source)
    try:
        assert con.execute(
            "SELECT COUNT(*) FROM pages WHERE id=?", (live["id"],)
        ).fetchone()[0] == 1
        assert con.execute(
            "SELECT COUNT(*) FROM pages WHERE id IN ('raw-page','raw-renamed')"
        ).fetchone()[0] == 0
    finally:
        con.close()


def test_raw_history_dml_is_blocked_and_check_bypass_is_detected(
    migrated: tuple[Path, Path, Path, dict, dict, dict]
) -> None:
    _root, source, _backups, live, _archived, _deleted = migrated
    row = _history(source, live["id"])[0]
    con = sqlite3.connect(source)
    con.row_factory = sqlite3.Row
    try:
        for sql in (
            "INSERT INTO notion_page_incarnation_history VALUES"
            "('raw-history',1,'inc_00000000000000000000000000000000',1,NULL)",
            "UPDATE notion_page_incarnation_history SET retired_at=9999999999 "
            f"WHERE page_id='{live['id']}'",
            "DELETE FROM notion_page_incarnation_history "
            f"WHERE page_id='{live['id']}'",
            "INSERT OR REPLACE INTO notion_page_incarnation_history VALUES"
            f"('{live['id']}',1,'{row[1]}',1,NULL)",
        ):
            with pytest.raises(sqlite3.Error):
                con.execute(sql)
                con.commit()
            con.rollback()
        assert inspect_legacy_incarnation_contract(con)["installed"] is True
        con.create_function("notion_incarnation_write_context", 0, lambda: 1)
        con.execute("PRAGMA ignore_check_constraints=ON")
        con.execute(
            "UPDATE notion_page_incarnation_history SET retired_at=created_at-1 "
            "WHERE page_id=?",
            (live["id"],),
        )
        con.commit()
        with pytest.raises(Exception, match="history is invalid"):
            inspect_legacy_incarnation_contract(con, verify_integrity=False)
    finally:
        con.close()


def test_v2_receipt_is_exact_secret_free_and_digest_matches_catalog(
    migrated: tuple[Path, Path, Path, dict, dict, dict]
) -> None:
    root, source, _backups, _live, archived, deleted = migrated
    archived_receipt = read_legacy_page_incarnation_receipt_v2(
        user_data_root=root, source_owner_id="owner",
        page_id=archived["id"], expected_source_db_path=source,
        source_quiesced_confirmed=True,
    )
    deleted_receipt = read_legacy_page_incarnation_receipt_v2(
        user_data_root=root, source_owner_id="owner",
        page_id=deleted["id"], expected_source_db_path=source,
        source_quiesced_confirmed=True,
    )
    assert archived_receipt.source_exists is True
    assert archived_receipt.source_active is False
    assert archived_receipt.incarnation_retired is False
    assert deleted_receipt.source_exists is False
    assert deleted_receipt.source_active is False
    assert deleted_receipt.incarnation_retired is True
    serialized = json.dumps(archived_receipt.__dict__, default=str)
    assert "SECRET" not in serialized
    assert str(source) not in serialized
    from code_assist_v1.notion_workspace_store import (
        legacy_incarnation_receipt_sha256 as catalog_digest,
    )
    kwargs = {
        "source_owner_id": archived_receipt.source_owner_id,
        "page_id": archived_receipt.page_id,
        "incarnation_id": archived_receipt.incarnation_id,
        "incarnation_generation": archived_receipt.incarnation_generation,
        "incarnation_retired": archived_receipt.incarnation_retired,
        "change_generation": archived_receipt.generation,
        "change_mask": archived_receipt.mask,
        "source_exists": archived_receipt.source_exists,
        "source_active": archived_receipt.source_active,
        "source_epoch": archived_receipt.fence.source_epoch,
        "full_sweep_generation": archived_receipt.fence.full_sweep_generation,
        "source_device": archived_receipt.fence.device,
        "source_inode": archived_receipt.fence.inode,
        "source_link_count": archived_receipt.fence.link_count,
    }
    assert archived_receipt.receipt_sha256 == catalog_digest(**kwargs)
    assert archived_receipt.receipt_sha256 == legacy_incarnation_receipt_sha256(**kwargs)


@pytest.mark.parametrize("trigger_table", ["pages", "blocks"])
def test_contract_rejects_every_extra_persistent_trigger(
    migrated: tuple[Path, Path, Path, dict, dict, dict],
    trigger_table: str,
) -> None:
    _root, source, _backups, _live, _archived, _deleted = migrated
    con = sqlite3.connect(source)
    con.row_factory = sqlite3.Row
    try:
        con.execute(
            f"""
            CREATE TRIGGER rogue_incarnation_{trigger_table}
            AFTER INSERT ON {trigger_table}
            BEGIN
              SELECT 1;
            END
            """
        )
        con.commit()
        with pytest.raises(Exception, match="trigger set is invalid"):
            inspect_legacy_incarnation_contract(con, verify_integrity=False)
        with pytest.raises(Exception, match="trigger set is invalid"):
            inspect_legacy_incarnation_contract(con, verify_integrity=True)
    finally:
        con.close()


def test_quiescence_precedes_path_access_and_high_bit_identity_is_canonical() -> None:
    with pytest.raises(LegacyChangeReadError, match="quiescence"):
        read_legacy_page_incarnation_receipt_v2(
            user_data_root=Path("Z:/definitely-missing"),
            source_owner_id="owner", page_id="page",
            expected_source_db_path=Path("Z:/definitely-missing/notion.db"),
            source_quiesced_confirmed=False,
        )
    assert sqlite_source_identity((1 << 64) - 1) == -1
    assert sqlite_source_identity(1 << 63) == -(1 << 63)
    with pytest.raises(ValueError):
        sqlite_source_identity(1 << 64)

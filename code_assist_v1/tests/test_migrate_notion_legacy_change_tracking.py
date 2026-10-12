from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys

import pytest

from code_assist_v1 import notion_store
from code_assist_v1 import notion_legacy_change_migration as legacy_migration
from code_assist_v1.notion_legacy_change_migration import (
    CHANGE_ALL,
    CHANGE_BLOCK,
    CHANGE_FILE,
    CHANGE_PAGE,
    CHANGE_PUBLIC,
    CHANGE_SHARE,
    LegacyChangeMigrationRefused,
    apply_plan,
    build_plan,
)


def test_legacy_change_schema_fingerprints_are_frozen() -> None:
    assert legacy_migration._MIGRATION_CHECKSUM == (
        "sha256:d110d59a175f98cae7851cc3fd130efdb2ae16f221657ceccc7689368166033b"
    )
    assert legacy_migration._EXPECTED_OBJECT_HASH == (
        "sha256:020c9cd1288120668e084d97f4e6304a7c9332ddb468436973496a4af301590e"
    )


def _tree_state(root: Path) -> tuple[tuple[str, int, int, str], ...]:
    result = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        stat = path.stat()
        result.append(
            (
                path.relative_to(root).as_posix(),
                stat.st_size,
                stat.st_mtime_ns,
                hashlib.sha256(path.read_bytes()).hexdigest(),
            )
        )
    return tuple(result)


def _fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = tmp_path / "user_data"
    root.mkdir()
    monkeypatch.setattr(notion_store, "USER_DATA_DIR", str(root))
    page = notion_store.create_page(
        "owner", title="ULTRA SECRET LEGACY TITLE"
    )["page"]
    source = root / "owner" / "notion" / "notion.db"
    backups = root / "migration-backups"
    backups.mkdir()
    return root, source, backups, page


def _apply(root: Path, source: Path, backups: Path):
    plan = build_plan(
        user_data_root=root,
        source_owner_id="owner",
        expected_source_db_path=source,
    )
    result = apply_plan(
        plan,
        confirm_checksum=plan["plan_checksum"],
        source_quiesced_confirmed=True,
        backup_confirmed=True,
        expected_backup_path=backups / "notion-before-change-tracking.db",
    )
    return plan, result


def _watermark(source: Path, page_id: str):
    con = sqlite3.connect(source)
    try:
        return con.execute(
            """
            SELECT change_generation,change_mask,source_exists
            FROM notion_page_change_watermarks WHERE page_id=?
            """,
            (page_id,),
        ).fetchone()
    finally:
        con.close()


def _security_metadata(path: Path):
    handle = legacy_migration._pin_windows_path(
        path, directory=False, allow_delete=True
    )
    try:
        return legacy_migration._windows_security_metadata(handle)[:2]
    finally:
        legacy_migration._close_path_pins([handle])


def test_dry_run_is_deterministic_read_only_and_apply_is_explicit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, source, backups, page = _fixture(tmp_path, monkeypatch)
    before = _tree_state(root)
    first = build_plan(
        user_data_root=root,
        source_owner_id="owner",
        expected_source_db_path=source,
    )
    second = build_plan(
        user_data_root=root,
        source_owner_id="owner",
        expected_source_db_path=source,
    )
    assert first == second
    assert first["action"] == "install"
    assert _tree_state(root) == before
    security_before = _security_metadata(source)

    plan, result = _apply(root, source, backups)
    assert result["status"] == "applied"
    assert result["plan_checksum"] == plan["plan_checksum"]
    backup_path = Path(result["backup"]["path"])
    assert backup_path.is_file()
    assert _security_metadata(backup_path) == security_before
    assert not Path(str(backup_path) + "-journal").exists()
    assert not Path(str(backup_path) + "-wal").exists()
    assert not Path(str(backup_path) + "-shm").exists()
    assert _watermark(source, page["id"]) == (1, CHANGE_ALL, 1)
    assert not Path(str(source) + "-journal").exists()
    assert not Path(str(source) + "-wal").exists()
    assert not list(source.parent.glob(".*.legacy-change-*.stage"))
    assert _security_metadata(source) == security_before
    current = build_plan(
        user_data_root=root,
        source_owner_id="owner",
        expected_source_db_path=source,
    )
    assert current["action"] == "already_current"


def test_triggers_cover_page_block_file_public_share_and_tombstone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, source, backups, page = _fixture(tmp_path, monkeypatch)
    _apply(root, source, backups)
    con = sqlite3.connect(source)
    try:
        con.execute("DELETE FROM notion_page_change_watermarks")
        con.commit()
    finally:
        con.close()

    notion_store.update_page("owner", page["id"], {"title": "changed"})
    first = _watermark(source, page["id"])
    assert first[1] & CHANGE_PAGE
    notion_store.replace_blocks(
        "owner",
        page["id"],
        [{"type": "paragraph", "content": "classified body", "props": {}}],
    )
    notion_store.upload_file(
        "owner", page["id"], filename="secret-filename.txt", raw=b"secret"
    )
    public = notion_store.enable_public_link("owner", page["id"])
    notion_store.share_page("owner", page["id"], "reader", "view")
    row = _watermark(source, page["id"])
    assert row[0] > first[0]
    assert row[1] == CHANGE_ALL
    assert row[2] == 1

    notion_store.archive_page("owner", page["id"])
    archived = _watermark(source, page["id"])
    assert archived[2] == 0
    payload = json.dumps(
        {
            "watermark": archived,
            "plan": build_plan(
                user_data_root=root,
                source_owner_id="owner",
                expected_source_db_path=source,
            ),
        },
        sort_keys=True,
    )
    assert "ULTRA SECRET" not in payload
    assert "classified body" not in payload
    assert "secret-filename" not in payload
    assert str(public["token"]) not in payload


def test_child_row_events_coalesce_and_page_delete_keeps_tombstone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, source, backups, page = _fixture(tmp_path, monkeypatch)
    _apply(root, source, backups)
    con = sqlite3.connect(source)
    try:
        con.execute("DELETE FROM notion_page_change_watermarks")
        now = 1
        con.execute(
            """
            INSERT INTO blocks(
              id,page_id,owner_user_id,position,block_type,content,props_json,
              created_at,updated_at
            ) VALUES('b_manual',?,?,0,'paragraph','x','{}',?,?)
            """,
            (page["id"], "owner", now, now),
        )
        con.execute("UPDATE blocks SET content='y' WHERE id='b_manual'")
        con.execute("DELETE FROM blocks WHERE id='b_manual'")
        con.commit()
        assert con.execute(
            "SELECT COUNT(*) FROM notion_page_change_watermarks"
        ).fetchone()[0] == 1
        generation, mask, exists = con.execute(
            "SELECT change_generation,change_mask,source_exists "
            "FROM notion_page_change_watermarks WHERE page_id=?",
            (page["id"],),
        ).fetchone()
        assert generation == 3
        assert mask == CHANGE_BLOCK
        assert exists == 1
        con.execute("DELETE FROM pages WHERE id=?", (page["id"],))
        con.commit()
        tombstone = con.execute(
            "SELECT change_mask,source_exists FROM notion_page_change_watermarks "
            "WHERE page_id=?",
            (page["id"],),
        ).fetchone()
        assert tombstone[0] & CHANGE_PAGE
        assert tombstone[1] == 0
    finally:
        con.close()


def test_inherited_share_public_and_parent_changes_mark_descendants(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, source, backups, parent = _fixture(tmp_path, monkeypatch)
    child = notion_store.create_page(
        "owner", title="child", parent_id=parent["id"]
    )["page"]
    grandchild = notion_store.create_page(
        "owner", title="grandchild", parent_id=child["id"]
    )["page"]
    destination = notion_store.create_page("owner", title="destination")["page"]
    _apply(root, source, backups)

    def clear() -> None:
        con = sqlite3.connect(source)
        try:
            con.execute("DELETE FROM notion_page_change_watermarks")
            con.commit()
        finally:
            con.close()

    clear()
    notion_store.share_page("owner", parent["id"], "reader", "view")
    for page_id in (parent["id"], child["id"], grandchild["id"]):
        assert _watermark(source, page_id)[1] & CHANGE_SHARE
    clear()
    notion_store.revoke_share("owner", parent["id"], "reader")
    assert _watermark(source, grandchild["id"])[1] & CHANGE_SHARE

    clear()
    notion_store.enable_public_link("owner", parent["id"])
    assert _watermark(source, child["id"])[1] & CHANGE_PUBLIC
    clear()
    notion_store.move_page(
        "owner", child["id"], parent_id=destination["id"]
    )
    assert _watermark(source, grandchild["id"])[1] & CHANGE_PAGE

    con = sqlite3.connect(source)
    try:
        state = con.execute(
            "SELECT source_epoch,full_sweep_generation "
            "FROM notion_legacy_change_state WHERE singleton=1"
        ).fetchone()
    finally:
        con.close()
    assert state[0] > 1
    assert state[1] > 1


def test_unsupported_source_column_affinity_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "user_data"
    notion_dir = root / "owner" / "notion"
    notion_dir.mkdir(parents=True)
    source = notion_dir / "notion.db"
    con = sqlite3.connect(source)
    try:
        con.executescript(
            """
            PRAGMA foreign_keys=ON;
            CREATE TABLE pages(id TEXT PRIMARY KEY,parent_id TEXT,archived TEXT NOT NULL);
            CREATE TABLE blocks(page_id TEXT NOT NULL REFERENCES pages(id) ON DELETE CASCADE);
            CREATE TABLE files(page_id TEXT NOT NULL REFERENCES pages(id) ON DELETE CASCADE);
            CREATE TABLE public_links(page_id TEXT PRIMARY KEY REFERENCES pages(id) ON DELETE CASCADE);
            CREATE TABLE page_shares(page_id TEXT NOT NULL REFERENCES pages(id) ON DELETE CASCADE);
            """
        )
        con.commit()
    finally:
        con.close()
    with pytest.raises(LegacyChangeMigrationRefused, match="unsupported"):
        build_plan(
            user_data_root=root,
            source_owner_id="owner",
            expected_source_db_path=source,
        )


def test_trigger_failure_rolls_back_the_legacy_domain_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, source, backups, page = _fixture(tmp_path, monkeypatch)
    _apply(root, source, backups)
    con = sqlite3.connect(source)
    try:
        con.executescript(
            """
            CREATE TRIGGER fail_change_watermark_update
            BEFORE UPDATE ON notion_page_change_watermarks
            BEGIN SELECT RAISE(ABORT,'injected watermark failure'); END;
            """
        )
        con.commit()
    finally:
        con.close()
    with pytest.raises(sqlite3.IntegrityError):
        notion_store.update_page("owner", page["id"], {"title": "must rollback"})
    assert notion_store.get_page("owner", page["id"])["page"]["title"] == (
        "ULTRA SECRET LEGACY TITLE"
    )


def test_apply_fault_rolls_back_schema_and_requires_new_backup_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, source, backups, _page = _fixture(tmp_path, monkeypatch)
    plan = build_plan(
        user_data_root=root,
        source_owner_id="owner",
        expected_source_db_path=source,
    )
    with pytest.raises(RuntimeError, match="fault"):
        apply_plan(
            plan,
            confirm_checksum=plan["plan_checksum"],
            source_quiesced_confirmed=True,
            backup_confirmed=True,
            expected_backup_path=backups / "rollback-backup.db",
            fault_injector=lambda phase: (
                (_ for _ in ()).throw(RuntimeError("fault"))
                if phase == "before_commit"
                else None
            ),
        )
    con = sqlite3.connect(source)
    try:
        assert con.execute(
            "SELECT 1 FROM sqlite_master WHERE name='notion_page_change_watermarks'"
        ).fetchone() is None
    finally:
        con.close()
    fresh = build_plan(
        user_data_root=root,
        source_owner_id="owner",
        expected_source_db_path=source,
    )
    assert fresh["action"] == "install"
    assert not Path(str(source) + "-journal").exists()
    assert not list(source.parent.glob(".*.legacy-change-*.stage"))


def test_wrong_path_hardlink_and_unconfirmed_apply_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, source, backups, _page = _fixture(tmp_path, monkeypatch)
    with pytest.raises(LegacyChangeMigrationRefused):
        build_plan(
            user_data_root=root,
            source_owner_id="owner",
            expected_source_db_path=root / "wrong.db",
        )
    plan = build_plan(
        user_data_root=root,
        source_owner_id="owner",
        expected_source_db_path=source,
    )
    with pytest.raises(LegacyChangeMigrationRefused, match="quiescence"):
        apply_plan(
            plan,
            confirm_checksum=plan["plan_checksum"],
            source_quiesced_confirmed=False,
            backup_confirmed=True,
            expected_backup_path=backups / "never-created.db",
        )
    alias = backups / "hardlink.db"
    try:
        os.link(source, alias)
    except OSError:
        pytest.skip("hard links are unavailable on this platform")
    with pytest.raises(LegacyChangeMigrationRefused, match="one link"):
        build_plan(
            user_data_root=root,
            source_owner_id="owner",
            expected_source_db_path=source,
        )


@pytest.mark.skipif(os.name != "nt", reason="Windows path-handle contract")
def test_windows_backup_create_is_anchored_to_verified_parent_handle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _source, backups, _page = _fixture(tmp_path, monkeypatch)
    target = backups / "pinned.db"
    pins = legacy_migration._acquire_path_pins(
        root=root,
        target=target,
        include_target=False,
    )
    moved = backups.with_name("migration-backups-moved")
    try:
        backups.rename(moved)
        backups.mkdir()
        pins.append(
            legacy_migration._create_pinned_windows_file(
                target,
                parent_handle=pins[-1],
            )
        )
        assert (moved / "pinned.db").is_file()
        assert not target.exists()
    finally:
        legacy_migration._close_path_pins(pins)


@pytest.mark.skipif(os.name != "nt", reason="Windows native leaf-name contract")
@pytest.mark.parametrize(
    "leaf",
    ("normal.db:secret", "CON.db", "trailing.db.", "trailing.db ", "bad?.db"),
)
def test_windows_backup_rejects_ads_devices_and_ambiguous_leaf_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, leaf: str
) -> None:
    root, _source, backups, _page = _fixture(tmp_path, monkeypatch)
    target = backups / leaf
    pins = legacy_migration._acquire_path_pins(
        root=root, target=target, include_target=False
    )
    try:
        with pytest.raises(LegacyChangeMigrationRefused, match="valid backup"):
            legacy_migration._create_pinned_windows_file(
                target, parent_handle=pins[-1], exclusive=True
            )
    finally:
        legacy_migration._close_path_pins(pins)
    assert leaf not in {item.name for item in backups.iterdir()}


@pytest.mark.skipif(os.name != "nt", reason="Windows security metadata contract")
def test_security_attribute_mismatch_fails_before_source_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, source, backups, _page = _fixture(tmp_path, monkeypatch)
    before = source.read_bytes()
    os.chmod(source, stat.S_IREAD)
    plan = build_plan(
        user_data_root=root,
        source_owner_id="owner",
        expected_source_db_path=source,
    )
    try:
        with pytest.raises(LegacyChangeMigrationRefused, match="security metadata"):
            apply_plan(
                plan,
                confirm_checksum=plan["plan_checksum"],
                source_quiesced_confirmed=True,
                backup_confirmed=True,
                expected_backup_path=backups / "readonly-source.db",
            )
        assert source.read_bytes() == before
        assert not list(source.parent.glob(".*.legacy-change-*.stage"))
    finally:
        os.chmod(source, stat.S_IWRITE | stat.S_IREAD)


@pytest.mark.skipif(os.name != "nt", reason="Windows security metadata contract")
def test_source_security_change_after_backup_invalidates_weaker_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, source, backups, _page = _fixture(tmp_path, monkeypatch)
    before = source.read_bytes()
    backup = backups / "security-drift.db"
    plan = build_plan(
        user_data_root=root,
        source_owner_id="owner",
        expected_source_db_path=source,
    )

    def harden_source(phase: str) -> None:
        if phase == "after_backup":
            os.chmod(source, stat.S_IREAD)

    try:
        with pytest.raises(LegacyChangeMigrationRefused, match="backup changed"):
            apply_plan(
                plan,
                confirm_checksum=plan["plan_checksum"],
                source_quiesced_confirmed=True,
                backup_confirmed=True,
                expected_backup_path=backup,
                fault_injector=harden_source,
            )
        assert source.read_bytes() == before
        assert not backup.exists()
    finally:
        os.chmod(source, stat.S_IWRITE | stat.S_IREAD)


def test_cleanup_failure_still_releases_all_backup_handles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, source, backups, _page = _fixture(tmp_path, monkeypatch)
    plan = build_plan(
        user_data_root=root,
        source_owner_id="owner",
        expected_source_db_path=source,
    )
    backup = backups / "cleanup-failure.db"

    monkeypatch.setattr(
        legacy_migration,
        "_write_flush_windows_handle",
        lambda _handle, _payload: (_ for _ in ()).throw(
            LegacyChangeMigrationRefused("injected write failure")
        ),
    )
    monkeypatch.setattr(
        legacy_migration,
        "_dispose_windows_handle_on_close",
        lambda _handle: (_ for _ in ()).throw(
            LegacyChangeMigrationRefused("injected cleanup failure")
        ),
    )
    with pytest.raises(LegacyChangeMigrationRefused, match="cleanup failure"):
        apply_plan(
            plan,
            confirm_checksum=plan["plan_checksum"],
            source_quiesced_confirmed=True,
            backup_confirmed=True,
            expected_backup_path=backup,
        )

    moved_source_parent = source.parent.with_name("notion-moved-after-cleanup")
    source.parent.rename(moved_source_parent)
    moved_source_parent.rename(source.parent)
    moved_backups = backups.with_name("migration-backups-moved-after-cleanup")
    backups.rename(moved_backups)
    moved_backups.rename(backups)


def test_existing_backup_is_never_overwritten_or_deleted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, source, backups, _page = _fixture(tmp_path, monkeypatch)
    plan = build_plan(
        user_data_root=root,
        source_owner_id="owner",
        expected_source_db_path=source,
    )
    existing = backups / "existing.db"
    existing.write_bytes(b"operator-owned-backup")
    with pytest.raises(LegacyChangeMigrationRefused, match="new exact path"):
        apply_plan(
            plan,
            confirm_checksum=plan["plan_checksum"],
            source_quiesced_confirmed=True,
            backup_confirmed=True,
            expected_backup_path=existing,
        )
    assert existing.read_bytes() == b"operator-owned-backup"


def test_backup_tamper_before_commit_rolls_back_source_schema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, source, backups, _page = _fixture(tmp_path, monkeypatch)
    plan = build_plan(
        user_data_root=root,
        source_owner_id="owner",
        expected_source_db_path=source,
    )
    backup = backups / "tamper.db"

    blocked: list[bool] = []

    def tamper(phase: str) -> None:
        if phase == "before_commit":
            try:
                backup.write_bytes(b"tampered")
            except OSError:
                blocked.append(True)
                raise RuntimeError("backup tamper blocked")
            raise AssertionError("verified backup unexpectedly accepted a writer")

    with pytest.raises(RuntimeError, match="tamper blocked"):
        apply_plan(
            plan,
            confirm_checksum=plan["plan_checksum"],
            source_quiesced_confirmed=True,
            backup_confirmed=True,
            expected_backup_path=backup,
            fault_injector=tamper,
        )
    assert blocked == [True]
    con = sqlite3.connect(source)
    try:
        assert con.execute(
            "SELECT 1 FROM sqlite_master WHERE name='notion_page_change_watermarks'"
        ).fetchone() is None
    finally:
        con.close()


def test_partial_native_backup_is_removed_without_sidecars(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, source, backups, _page = _fixture(tmp_path, monkeypatch)
    plan = build_plan(
        user_data_root=root,
        source_owner_id="owner",
        expected_source_db_path=source,
    )
    backup = backups / "partial.db"
    original = legacy_migration._write_flush_windows_handle

    def fail_after_partial_write(handle: int, payload: bytes) -> None:
        original(handle, payload[: max(1, len(payload) // 2)])
        raise LegacyChangeMigrationRefused("injected backup write failure")

    monkeypatch.setattr(
        legacy_migration, "_write_flush_windows_handle", fail_after_partial_write
    )
    with pytest.raises(LegacyChangeMigrationRefused, match="injected"):
        apply_plan(
            plan,
            confirm_checksum=plan["plan_checksum"],
            source_quiesced_confirmed=True,
            backup_confirmed=True,
            expected_backup_path=backup,
        )
    assert not backup.exists()
    assert not Path(str(backup) + "-journal").exists()
    assert not Path(str(backup) + "-wal").exists()
    assert not Path(str(backup) + "-shm").exists()


def test_post_replacement_failure_preserves_verified_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, source, backups, _page = _fixture(tmp_path, monkeypatch)
    plan = build_plan(
        user_data_root=root,
        source_owner_id="owner",
        expected_source_db_path=source,
    )
    backup = backups / "post-replacement.db"

    with pytest.raises(RuntimeError, match="post replacement audit"):
        apply_plan(
            plan,
            confirm_checksum=plan["plan_checksum"],
            source_quiesced_confirmed=True,
            backup_confirmed=True,
            expected_backup_path=backup,
            fault_injector=lambda phase: (
                (_ for _ in ()).throw(RuntimeError("post replacement audit"))
                if phase == "after_replacement"
                else None
            ),
        )
    assert backup.is_file()
    con = sqlite3.connect(source)
    try:
        assert con.execute(
            "SELECT 1 FROM sqlite_master WHERE name='notion_page_change_watermarks'"
        ).fetchone() == (1,)
    finally:
        con.close()


def test_native_replace_exception_converges_without_disposing_live_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, source, backups, _page = _fixture(tmp_path, monkeypatch)
    plan = build_plan(
        user_data_root=root,
        source_owner_id="owner",
        expected_source_db_path=source,
    )
    backup = backups / "ambiguous-replace.db"
    original_replace = legacy_migration._replace_windows_handle_relative

    def committed_then_error(*args, **kwargs):
        original_replace(*args, **kwargs)
        raise LegacyChangeMigrationRefused("simulated native transport error")

    monkeypatch.setattr(
        legacy_migration,
        "_replace_windows_handle_relative",
        committed_then_error,
    )
    result = apply_plan(
        plan,
        confirm_checksum=plan["plan_checksum"],
        source_quiesced_confirmed=True,
        backup_confirmed=True,
        expected_backup_path=backup,
    )
    assert result["status"] == "applied"
    assert result["commit_recovered"] is True
    assert source.is_file()
    assert backup.is_file()
    con = sqlite3.connect(source)
    try:
        assert con.execute(
            "SELECT 1 FROM sqlite_master WHERE name='notion_page_change_watermarks'"
        ).fetchone() == (1,)
    finally:
        con.close()


def test_unknown_native_replace_outcome_never_deletes_live_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, source, backups, _page = _fixture(tmp_path, monkeypatch)
    plan = build_plan(
        user_data_root=root,
        source_owner_id="owner",
        expected_source_db_path=source,
    )
    backup = backups / "unknown-replace.db"
    original_replace = legacy_migration._replace_windows_handle_relative
    original_final_path = legacy_migration._windows_final_path
    replacement_attempted = False

    def committed_then_error(*args, **kwargs):
        nonlocal replacement_attempted
        original_replace(*args, **kwargs)
        replacement_attempted = True
        raise LegacyChangeMigrationRefused("unprovable native outcome")

    def unavailable_path(handle):
        if replacement_attempted:
            raise LegacyChangeMigrationRefused("final path unavailable")
        return original_final_path(handle)

    monkeypatch.setattr(
        legacy_migration,
        "_replace_windows_handle_relative",
        committed_then_error,
    )
    monkeypatch.setattr(legacy_migration, "_windows_final_path", unavailable_path)
    with pytest.raises(LegacyChangeMigrationRefused) as caught:
        apply_plan(
            plan,
            confirm_checksum=plan["plan_checksum"],
            source_quiesced_confirmed=True,
            backup_confirmed=True,
            expected_backup_path=backup,
        )
    assert str(caught.value) == (
        "replacement_outcome_indeterminate: manual audit required"
    )
    assert source.is_file()
    assert backup.is_file()


def test_module_import_is_app_and_filesystem_side_effect_free(tmp_path: Path) -> None:
    probe = tmp_path / "probe"
    probe.mkdir()
    script = (
        "import json,sys; import code_assist_v1.notion_legacy_change_migration; "
        "forbidden=('code_assist_v1.app_code','code_assist_v1.app_notion',"
        "'code_assist_v1.routes_notion_bridge','code_assist_v1.second_brain_store',"
        "'code_assist_v1.knowledge_core'); "
        "print(json.dumps([x for x in forbidden if x in sys.modules]))"
    )
    environment = os.environ.copy()
    environment["CODE_ASSIST_DATA_ROOT"] = str(probe)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    before = tuple(probe.iterdir())
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[2],
        env=environment,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == []
    assert tuple(probe.iterdir()) == before

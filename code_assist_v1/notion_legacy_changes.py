"""Bounded read-only access to explicitly migrated legacy change watermarks."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import sqlite3

from code_assist_v1.notion_legacy_change_contract import (
    CHANGE_ALL,
    LegacyChangeContractError,
    MAX_GENERATION,
    assert_legacy_rollback_journal,
    inspect_legacy_change_contract,
    legacy_source_file_identity,
    open_exact_legacy_change_source,
    resolve_legacy_change_paths,
)
from code_assist_v1.notion_legacy_incarnation_contract import (
    LegacyIncarnationContractError,
    inspect_legacy_incarnation_contract,
    require_legacy_page_incarnation_id,
)


MAX_CHANGE_BATCH = 32
LEGACY_INCARNATION_RECEIPT_DOMAIN = (
    "code_assist.notion.legacy_page_incarnation_receipt.v1"
)


class LegacyChangeReadError(Exception):
    """Sanitized fail-closed read error."""


# Preserve the test seam used by the original bounded scanner while keeping
# the runtime reader independent from the executable migration module.
_inspect = inspect_legacy_change_contract


def inspect_legacy_incarnation_source_contract(
    con: sqlite3.Connection, *, verify_integrity: bool = True
) -> dict[str, object]:
    """Authenticate an exact v2 source or its frozen additive v3 successor.

    The import is deliberately lazy because the v3 contract reuses this
    module's canonical receipt digest.  Request paths still never import the
    executable v3 migrator or its CLI.
    """
    try:
        from code_assist_v1.notion_legacy_change_consumer_contract import (
            inspect_legacy_change_consumer_contract,
        )

        result = inspect_legacy_change_consumer_contract(
            con, verify_integrity=verify_integrity
        )
        return {
            "installed": True,
            "contract_version": 3 if result["installed"] else 2,
            "object_hash": (
                str(result["object_hash"])
                if result["installed"]
                else str(result["v2_object_hash"])
            ),
        }
    except LegacyIncarnationContractError:
        raise
    except Exception as exc:
        raise LegacyIncarnationContractError(
            "legacy incarnation schema is invalid"
        ) from exc


def _inspect_source_contract(
    con: sqlite3.Connection, *, verify_integrity: bool
) -> dict[str, object]:
    successor = con.execute(
        "SELECT 1 FROM notion_legacy_schema_migrations "
        "WHERE version IN (2026081302,2026081303) LIMIT 1"
    ).fetchone()
    if successor:
        return inspect_legacy_incarnation_source_contract(
            con, verify_integrity=verify_integrity
        )
    return _inspect(con, verify_integrity=verify_integrity)


@dataclass(frozen=True)
class LegacyChangeCursor:
    """Internal keyset cursor; it is not a public/API cursor."""

    page_id: str
    source_epoch: int
    full_sweep_generation: int
    source_device: int
    source_inode: int
    source_link_count: int


@dataclass(frozen=True)
class LegacyPageChange:
    page_id: str
    generation: int
    change_mask: int
    changed_at: int
    source_exists: bool


@dataclass(frozen=True)
class LegacyPageChangeBatch:
    items: tuple[LegacyPageChange, ...]
    next_cursor: LegacyChangeCursor | None
    has_more: bool
    scanned_count: int
    source_epoch: int
    full_sweep_generation: int


@dataclass(frozen=True)
class LegacySourceFence:
    """Exact source-file and change-state identity for one read snapshot."""

    device: int
    inode: int
    link_count: int
    source_epoch: int
    full_sweep_generation: int


@dataclass(frozen=True)
class LegacyOwnerIntegrityAttestation:
    """Secret-free integrity proof for one exact, quiesced owner shard.

    The attestation deliberately contains no filesystem path, page content,
    attachment metadata, or public token.  Its complete file identity prevents
    a later lightweight contract inspection from being reused for a different
    source image.
    """

    source_owner_id: str
    source_device: int
    source_inode: int
    source_link_count: int
    source_size: int
    source_mtime_ns: int
    source_epoch: int
    full_sweep_generation: int
    object_hash: str

    @property
    def source_identity(self) -> tuple[int, int, int, int, int]:
        return (
            self.source_device,
            self.source_inode,
            self.source_link_count,
            self.source_size,
            self.source_mtime_ns,
        )


class LegacyOwnerIntegrityAttestationMismatch(LegacyChangeReadError):
    """The exact owner source no longer matches its integrity attestation."""


@dataclass(frozen=True)
class LegacyPageChangeReceipt:
    """Secret-free exact watermark receipt for one owner/page snapshot."""

    page_id: str
    generation: int
    mask: int
    source_exists: bool
    source_active: bool
    fence: LegacySourceFence

    @property
    def change_mask(self) -> int:
        """Compatibility spelling for code that consumes scan rows."""
        return self.mask


@dataclass(frozen=True)
class LegacyPageIncarnationReceipt:
    """Secret-free exact v2 source identity and change receipt."""

    source_owner_id: str
    page_id: str
    incarnation_id: str
    incarnation_generation: int
    incarnation_retired: bool
    generation: int
    mask: int
    source_exists: bool
    source_active: bool
    fence: LegacySourceFence

    @property
    def change_generation(self) -> int:
        return self.generation

    @property
    def change_mask(self) -> int:
        return self.mask

    @property
    def receipt_sha256(self) -> str:
        return legacy_incarnation_receipt_sha256(
            source_owner_id=self.source_owner_id,
            page_id=self.page_id,
            incarnation_id=self.incarnation_id,
            incarnation_generation=self.incarnation_generation,
            incarnation_retired=self.incarnation_retired,
            change_generation=self.generation,
            change_mask=self.mask,
            source_exists=self.source_exists,
            source_active=self.source_active,
            source_epoch=self.fence.source_epoch,
            full_sweep_generation=self.fence.full_sweep_generation,
            source_device=self.fence.device,
            source_inode=self.fence.inode,
            source_link_count=self.fence.link_count,
        )


def sqlite_source_identity(value: int) -> int:
    """Canonicalize an OS identity to the signed SQLite int64 domain."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("source identity must be an integer")
    if -(1 << 63) <= value <= (1 << 63) - 1:
        return value
    if (1 << 63) <= value <= (1 << 64) - 1:
        return value - (1 << 64)
    raise ValueError("source identity exceeds the SQLite int64 domain")


def legacy_incarnation_receipt_sha256(
    *,
    source_owner_id: str,
    page_id: str,
    incarnation_id: str,
    incarnation_generation: int,
    incarnation_retired: bool,
    change_generation: int,
    change_mask: int,
    source_exists: bool,
    source_active: bool,
    source_epoch: int,
    full_sweep_generation: int,
    source_device: int,
    source_inode: int,
    source_link_count: int,
) -> str:
    """Hash the byte-identical catalog/legacy incarnation receipt envelope."""
    payload = {
        "domain": LEGACY_INCARNATION_RECEIPT_DOMAIN,
        "receipt": {
            "source_owner_id": source_owner_id,
            "page_id": page_id,
            "incarnation_id": incarnation_id,
            "incarnation_generation": incarnation_generation,
            "incarnation_retired": incarnation_retired,
            "change_generation": change_generation,
            "change_mask": change_mask,
            "source_exists": source_exists,
            "source_active": source_active,
            "source_epoch": source_epoch,
            "full_sweep_generation": full_sweep_generation,
            "source_device": sqlite_source_identity(source_device),
            "source_inode": sqlite_source_identity(source_inode),
            "source_link_count": source_link_count,
        },
    }
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _page_id(value: object, *, field: str) -> str:
    if not isinstance(value, str):
        raise LegacyChangeReadError(f"valid {field} required")
    result = value
    if not result or result != result.strip() or len(result) > 160:
        raise LegacyChangeReadError(f"valid {field} required")
    return result


def _change_from_row(row: object) -> LegacyPageChange:
    """Decode an untrusted SQLite row without leaking conversion failures."""
    try:
        page_id = _page_id(row["page_id"], field="page_id")  # type: ignore[index]
        generation = row["change_generation"]  # type: ignore[index]
        change_mask = row["change_mask"]  # type: ignore[index]
        changed_at = row["changed_at"]  # type: ignore[index]
        source_exists = row["source_exists"]  # type: ignore[index]
    except LegacyChangeReadError:
        raise
    except Exception as exc:
        raise LegacyChangeReadError("legacy change tracking is invalid") from exc
    if (
        type(generation) is not int
        or generation < 1
        or type(change_mask) is not int
        or not 1 <= change_mask <= CHANGE_ALL
        or type(changed_at) is not int
        or changed_at < 0
        or type(source_exists) is not int
        or source_exists not in (0, 1)
    ):
        raise LegacyChangeReadError("legacy change tracking is invalid")
    return LegacyPageChange(
        page_id=page_id,
        generation=generation,
        change_mask=change_mask,
        changed_at=changed_at,
        source_exists=bool(source_exists),
    )


def _positive_generation(value: object, *, field: str) -> int:
    if type(value) is not int or not 1 <= value <= MAX_GENERATION:
        raise LegacyChangeReadError("legacy change tracking is invalid")
    return value


def _state_and_fence(
    con: object,
    source_identity: tuple[int, int, int, int, int],
) -> LegacySourceFence:
    try:
        state = con.execute(  # type: ignore[attr-defined]
            "SELECT source_epoch,full_sweep_generation "
            "FROM notion_legacy_change_state WHERE singleton=1"
        ).fetchone()
        if not state:
            raise LegacyChangeReadError("legacy change tracking is invalid")
        source_epoch = _positive_generation(
            state["source_epoch"], field="source_epoch"
        )
        full_sweep_generation = _positive_generation(
            state["full_sweep_generation"], field="full_sweep_generation"
        )
        return LegacySourceFence(
            device=source_identity[0],
            inode=source_identity[1],
            link_count=source_identity[2],
            source_epoch=source_epoch,
            full_sweep_generation=full_sweep_generation,
        )
    except LegacyChangeReadError:
        raise
    except Exception as exc:
        raise LegacyChangeReadError("legacy change tracking is invalid") from exc


def _receipt_from_snapshot(
    con: object,
    *,
    source_owner_id: str,
    page_id: str,
    source_identity: tuple[int, int, int, int, int],
) -> LegacyPageChangeReceipt:
    """Decode one receipt from an already authenticated source snapshot."""
    exact_page_id = _page_id(page_id, field="page_id")
    fence = _state_and_fence(con, source_identity)
    try:
        row = con.execute(  # type: ignore[attr-defined]
            "SELECT page_id,change_generation,change_mask,changed_at,source_exists "
            "FROM notion_page_change_watermarks WHERE page_id=?",
            (exact_page_id,),
        ).fetchone()
        if not row:
            raise LegacyChangeReadError("legacy page change receipt is unavailable")
        generation = _positive_generation(
            row["change_generation"], field="change_generation"
        )
        mask = row["change_mask"]
        changed_at = row["changed_at"]
        watermark_active = row["source_exists"]
        if (
            _page_id(row["page_id"], field="page_id") != exact_page_id
            or type(mask) is not int
            or not 1 <= mask <= CHANGE_ALL
            or type(changed_at) is not int
            or changed_at < 0
            or type(watermark_active) is not int
            or watermark_active not in (0, 1)
        ):
            raise LegacyChangeReadError("legacy change tracking is invalid")
        page = con.execute(  # type: ignore[attr-defined]
            "SELECT owner_user_id,archived FROM pages WHERE id=? LIMIT 1",
            (exact_page_id,),
        ).fetchone()
        if page is not None and (
            str(page["owner_user_id"]) != source_owner_id
            or type(page["archived"]) is not int
            or page["archived"] not in (0, 1)
        ):
            raise LegacyChangeReadError("legacy change tracking is invalid")
        page_exists = page is not None
        page_active = bool(page_exists and page["archived"] == 0)
        # The frozen migration calls this column source_exists, but its trigger
        # contract records active existence (archived=0).  Normalize the public
        # receipt to physical row existence so archive and delete remain
        # distinguishable, while authenticating the raw bit against page state.
        if page_active is not bool(watermark_active):
            raise LegacyChangeReadError("legacy change tracking is invalid")
        return LegacyPageChangeReceipt(
            page_id=exact_page_id,
            generation=generation,
            mask=mask,
            source_exists=page_exists,
            source_active=page_active,
            fence=fence,
        )
    except LegacyChangeReadError:
        raise
    except Exception as exc:
        raise LegacyChangeReadError("legacy change tracking is invalid") from exc


def _exact_source_snapshot(
    *,
    user_data_root: str | os.PathLike[str],
    source_owner_id: str,
    expected_source_db_path: str | os.PathLike[str],
) -> tuple[dict[str, Path], tuple[int, int, int, int, int], sqlite3.Connection]:
    paths = resolve_legacy_change_paths(
        user_data_root=user_data_root,
        source_owner_id=source_owner_id,
        expected_source_db_path=expected_source_db_path,
    )
    assert_legacy_rollback_journal(paths["source"])
    source_identity = legacy_source_file_identity(paths["source"])
    con = open_exact_legacy_change_source(
        paths["source"],
        root=paths["root"],
        expected_resolved=paths["source"],
        expected_identity=source_identity,
    )
    return paths, source_identity, con


def _authenticate_owner_snapshot(
    con: sqlite3.Connection,
    *,
    source_owner_id: str,
    source_identity: tuple[int, int, int, int, int],
    integrity_attestation: LegacyOwnerIntegrityAttestation | None,
) -> tuple[LegacySourceFence, str]:
    """Authenticate one pinned snapshot, using a prior full attestation.

    Without an attestation this retains the historical full-integrity behavior.
    With one, the expensive database-wide checks are skipped, but the frozen
    schema contract, complete file identity, owner, and change-state fence must
    all remain exact.
    """
    if integrity_attestation is not None:
        if not isinstance(integrity_attestation, LegacyOwnerIntegrityAttestation):
            raise LegacyOwnerIntegrityAttestationMismatch(
                "legacy owner integrity attestation is invalid"
            )
        if (
            integrity_attestation.source_owner_id != source_owner_id
            or integrity_attestation.source_identity != source_identity
        ):
            raise LegacyOwnerIntegrityAttestationMismatch(
                "legacy owner integrity attestation changed"
            )
    inspected = _inspect_source_contract(
        con, verify_integrity=integrity_attestation is None
    )
    if not inspected["installed"]:
        raise LegacyChangeReadError("legacy change tracking is unavailable")
    fence = _state_and_fence(con, source_identity)
    object_hash = str(inspected.get("object_hash") or "")
    if integrity_attestation is not None and (
        object_hash != integrity_attestation.object_hash
        or fence.source_epoch != integrity_attestation.source_epoch
        or fence.full_sweep_generation
        != integrity_attestation.full_sweep_generation
    ):
        raise LegacyOwnerIntegrityAttestationMismatch(
            "legacy owner integrity attestation changed"
        )
    return fence, object_hash


def create_legacy_owner_integrity_attestation(
    *,
    user_data_root: str | os.PathLike[str],
    source_owner_id: str,
    expected_source_db_path: str | os.PathLike[str],
    source_quiesced_confirmed: bool,
) -> LegacyOwnerIntegrityAttestation:
    """Perform one full integrity inspection for a quiesced owner shard."""
    # Keep the operator assertion as the first gate before path or SQLite I/O.
    if source_quiesced_confirmed is not True:
        raise LegacyChangeReadError(
            "integrity attestation requires source quiescence"
        )
    con = None
    try:
        paths, source_identity, con = _exact_source_snapshot(
            user_data_root=user_data_root,
            source_owner_id=source_owner_id,
            expected_source_db_path=expected_source_db_path,
        )
        con.execute("BEGIN")
        fence, object_hash = _authenticate_owner_snapshot(
            con,
            source_owner_id=paths["owner"].name,
            source_identity=source_identity,
            integrity_attestation=None,
        )
        con.rollback()
        assert_legacy_rollback_journal(paths["source"])
        if legacy_source_file_identity(paths["source"]) != source_identity:
            raise LegacyOwnerIntegrityAttestationMismatch(
                "legacy owner integrity attestation changed"
            )
        return LegacyOwnerIntegrityAttestation(
            source_owner_id=paths["owner"].name,
            source_device=source_identity[0],
            source_inode=source_identity[1],
            source_link_count=source_identity[2],
            source_size=source_identity[3],
            source_mtime_ns=source_identity[4],
            source_epoch=fence.source_epoch,
            full_sweep_generation=fence.full_sweep_generation,
            object_hash=object_hash,
        )
    except LegacyChangeReadError:
        raise
    except LegacyChangeContractError as exc:
        raise LegacyChangeReadError("legacy change tracking is unavailable") from exc
    except Exception as exc:
        raise LegacyChangeReadError("legacy change tracking is unavailable") from exc
    finally:
        if con is not None:
            try:
                con.close()
            except Exception:
                pass


def read_legacy_page_change_receipt(
    *,
    user_data_root: str | os.PathLike[str],
    source_owner_id: str,
    page_id: str,
    expected_source_db_path: str | os.PathLike[str],
    source_quiesced_confirmed: bool,
    integrity_attestation: LegacyOwnerIntegrityAttestation | None = None,
) -> LegacyPageChangeReceipt:
    """Read one exact watermark and page-existence fact in one RO snapshot."""
    # This must precede every path conversion, resolution, stat, and SQLite
    # access so a false operator assertion has no source-side effects.
    if source_quiesced_confirmed is not True:
        raise LegacyChangeReadError("change receipt requires source quiescence")
    exact_page_id = _page_id(page_id, field="page_id")
    con = None
    try:
        paths, source_identity, con = _exact_source_snapshot(
            user_data_root=user_data_root,
            source_owner_id=source_owner_id,
            expected_source_db_path=expected_source_db_path,
        )
        con.execute("BEGIN")
        _authenticate_owner_snapshot(
            con,
            source_owner_id=paths["owner"].name,
            source_identity=source_identity,
            integrity_attestation=integrity_attestation,
        )
        receipt = _receipt_from_snapshot(
            con,
            source_owner_id=paths["owner"].name,
            page_id=exact_page_id,
            source_identity=source_identity,
        )
        con.rollback()
        assert_legacy_rollback_journal(paths["source"])
        if legacy_source_file_identity(paths["source"]) != source_identity:
            raise LegacyChangeReadError("legacy change source changed during read")
        return receipt
    except LegacyChangeReadError:
        raise
    except LegacyChangeContractError as exc:
        raise LegacyChangeReadError("legacy change tracking is unavailable") from exc
    except Exception as exc:
        raise LegacyChangeReadError("legacy change tracking is unavailable") from exc
    finally:
        if con is not None:
            try:
                con.close()
            except Exception:
                pass


def read_legacy_page_incarnation_receipt_v2(
    *,
    user_data_root: str | os.PathLike[str],
    source_owner_id: str,
    page_id: str,
    expected_source_db_path: str | os.PathLike[str],
    source_quiesced_confirmed: bool,
) -> LegacyPageIncarnationReceipt:
    """Read one exact v2 incarnation+watermark receipt from a RO snapshot.

    This is intentionally a new entry point.  The frozen v1 reader and its
    attestation type retain their original schema/API semantics.
    """
    if source_quiesced_confirmed is not True:
        raise LegacyChangeReadError(
            "incarnation receipt requires source quiescence"
        )
    exact_page_id = _page_id(page_id, field="page_id")
    con = None
    try:
        paths, source_identity, con = _exact_source_snapshot(
            user_data_root=user_data_root,
            source_owner_id=source_owner_id,
            expected_source_db_path=expected_source_db_path,
        )
        con.execute("BEGIN")
        inspected = inspect_legacy_incarnation_source_contract(con)
        if not inspected["installed"]:
            raise LegacyChangeReadError(
                "legacy incarnation tracking is unavailable"
            )
        v1_receipt = _receipt_from_snapshot(
            con,
            source_owner_id=paths["owner"].name,
            page_id=exact_page_id,
            source_identity=source_identity,
        )
        row = con.execute(
            """
            SELECT incarnation_generation,incarnation_id,retired_at
            FROM notion_page_incarnation_history
            WHERE page_id=? ORDER BY incarnation_generation DESC LIMIT 1
            """,
            (exact_page_id,),
        ).fetchone()
        if not row:
            raise LegacyChangeReadError(
                "legacy page incarnation receipt is unavailable"
            )
        incarnation_generation = _positive_generation(
            row["incarnation_generation"], field="incarnation_generation"
        )
        incarnation_id = require_legacy_page_incarnation_id(
            row["incarnation_id"]
        )
        retired_at = row["retired_at"]
        if retired_at is not None and (
            type(retired_at) is not int or retired_at < 0
        ):
            raise LegacyChangeReadError("legacy incarnation history is invalid")
        retired = retired_at is not None
        if retired is v1_receipt.source_exists:
            raise LegacyChangeReadError("legacy incarnation history is invalid")
        receipt = LegacyPageIncarnationReceipt(
            source_owner_id=paths["owner"].name,
            page_id=exact_page_id,
            incarnation_id=incarnation_id,
            incarnation_generation=incarnation_generation,
            incarnation_retired=retired,
            generation=v1_receipt.generation,
            mask=v1_receipt.mask,
            source_exists=v1_receipt.source_exists,
            source_active=v1_receipt.source_active,
            fence=v1_receipt.fence,
        )
        con.rollback()
        assert_legacy_rollback_journal(paths["source"])
        if legacy_source_file_identity(paths["source"]) != source_identity:
            raise LegacyChangeReadError("legacy change source changed during read")
        return receipt
    except LegacyChangeReadError:
        raise
    except (LegacyChangeContractError, LegacyIncarnationContractError) as exc:
        raise LegacyChangeReadError(
            "legacy incarnation tracking is unavailable"
        ) from exc
    except Exception as exc:
        raise LegacyChangeReadError(
            "legacy incarnation tracking is unavailable"
        ) from exc
    finally:
        if con is not None:
            try:
                con.close()
            except Exception:
                pass


def scan_legacy_page_changes(
    *,
    user_data_root: str | os.PathLike[str],
    source_owner_id: str,
    expected_source_db_path: str | os.PathLike[str],
    source_quiesced_confirmed: bool,
    after: LegacyChangeCursor | None = None,
    limit: int = MAX_CHANGE_BATCH,
) -> LegacyPageChangeBatch:
    """Read one bounded page-id keyset from one exact owner shard."""
    if source_quiesced_confirmed is not True:
        raise LegacyChangeReadError("change scan requires source quiescence")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_CHANGE_BATCH:
        raise LegacyChangeReadError("limit must be between 1 and 32")
    after_page = ""
    cursor_epoch = 0
    cursor_sweep = 0
    cursor_identity: tuple[int, int, int] | None = None
    if after is not None:
        if not isinstance(after, LegacyChangeCursor):
            raise LegacyChangeReadError("LegacyChangeCursor required")
        after_page = _page_id(after.page_id, field="cursor page_id")
        cursor_epoch = after.source_epoch
        cursor_sweep = after.full_sweep_generation
        cursor_identity = (
            after.source_device,
            after.source_inode,
            after.source_link_count,
        )
        if (
            type(cursor_epoch) is not int
            or cursor_epoch < 1
            or type(cursor_sweep) is not int
            or cursor_sweep < 1
            or any(type(value) is not int or value < 0 for value in cursor_identity)
        ):
            raise LegacyChangeReadError("legacy change cursor is invalid")
    try:
        paths = resolve_legacy_change_paths(
            user_data_root=user_data_root,
            source_owner_id=source_owner_id,
            expected_source_db_path=expected_source_db_path,
        )
        assert_legacy_rollback_journal(paths["source"])
        source_identity = legacy_source_file_identity(paths["source"])
        if cursor_identity is not None and source_identity[:3] != cursor_identity:
            raise LegacyChangeReadError("legacy change source changed during scan")
        con = open_exact_legacy_change_source(
            paths["source"],
            root=paths["root"],
            expected_resolved=paths["source"],
            expected_identity=source_identity,
        )
        try:
            con.execute("BEGIN")
            if not _inspect_source_contract(
                con, verify_integrity=after is None
            )["installed"]:
                raise LegacyChangeReadError("legacy change tracking is unavailable")
            state = con.execute(
                "SELECT source_epoch,full_sweep_generation "
                "FROM notion_legacy_change_state WHERE singleton=1"
            ).fetchone()
            if not state:
                raise LegacyChangeReadError("legacy change tracking is invalid")
            source_epoch = state["source_epoch"]
            full_sweep_generation = state["full_sweep_generation"]
            if (
                type(source_epoch) is not int
                or source_epoch < 1
                or type(full_sweep_generation) is not int
                or full_sweep_generation < 1
            ):
                raise LegacyChangeReadError("legacy change tracking is invalid")
            if after is not None and (
                source_epoch != cursor_epoch
                or full_sweep_generation != cursor_sweep
            ):
                raise LegacyChangeReadError("legacy change source changed during scan")
            rows = con.execute(
                """
                SELECT page_id,change_generation,change_mask,changed_at,source_exists
                FROM notion_page_change_watermarks
                WHERE page_id>? ORDER BY page_id LIMIT ?
                """,
                (after_page, limit + 1),
            ).fetchall()
            con.rollback()
        finally:
            con.close()
        assert_legacy_rollback_journal(paths["source"])
    except LegacyChangeReadError:
        raise
    except LegacyChangeContractError as exc:
        raise LegacyChangeReadError("legacy change tracking is unavailable") from exc
    except Exception as exc:
        raise LegacyChangeReadError("legacy change tracking is unavailable") from exc

    has_more = len(rows) > limit
    window = rows[:limit]
    items = tuple(_change_from_row(row) for row in window)
    cursor = (
        LegacyChangeCursor(
            page_id=items[-1].page_id,
            source_epoch=source_epoch,
            full_sweep_generation=full_sweep_generation,
            source_device=source_identity[0],
            source_inode=source_identity[1],
            source_link_count=source_identity[2],
        )
        if has_more and items
        else None
    )
    return LegacyPageChangeBatch(
        items=items,
        next_cursor=cursor,
        has_more=has_more,
        scanned_count=len(items),
        source_epoch=source_epoch,
        full_sweep_generation=full_sweep_generation,
    )


__all__ = [
    "LegacyChangeCursor",
    "LegacyChangeReadError",
    "LegacyPageChangeReceipt",
    "LegacyPageIncarnationReceipt",
    "LegacyPageChange",
    "LegacyPageChangeBatch",
    "LegacyOwnerIntegrityAttestation",
    "LegacyOwnerIntegrityAttestationMismatch",
    "LegacySourceFence",
    "MAX_CHANGE_BATCH",
    "LEGACY_INCARNATION_RECEIPT_DOMAIN",
    "create_legacy_owner_integrity_attestation",
    "read_legacy_page_change_receipt",
    "read_legacy_page_incarnation_receipt_v2",
    "legacy_incarnation_receipt_sha256",
    "inspect_legacy_incarnation_source_contract",
    "sqlite_source_identity",
    "scan_legacy_page_changes",
]

"""Durable, bounded, non-destructive legacy watermark materialization.

This module is deliberately connection-scoped.  A later exact-path offline
orchestrator owns filesystem validation and connection lifetime; request and
startup paths do not import or register it.  The source transaction copies at
most 32 exact v2 receipts into immutable work rows and never deletes or clears
``notion_page_change_watermarks``.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
import sqlite3
import time
from typing import Any, Callable

from code_assist_v1.notion_legacy_change_consumer_contract import (
    CONSUMER_NOW_UDF,
    CONSUMER_WRITE_CONTEXT_UDF,
    FULL_SWEEP_COMPLETION_ACTION,
    FULL_SWEEP_OPERATION_PREFIX,
    FULL_SWEEP_SET_COMPLETION_ACTION,
    FULL_SWEEP_SET_OPERATION_PREFIX,
    LegacyChangeConsumerContractError,
    MAX_BATCH_ROWS,
    MAX_LEASE_SECONDS,
    MAX_TIMESTAMP,
    inspect_legacy_change_consumer_contract,
    validate_full_sweep_completion_result,
    validate_full_sweep_set_completion_result,
)
from code_assist_v1.notion_legacy_changes import (
    LegacySourceFence,
    legacy_incarnation_receipt_sha256,
    sqlite_source_identity,
)


MAX_SEQUENCE = 9_007_199_254_740_991
_CONSUMER_RE = re.compile(r"^[a-z][a-z0-9_.-]{1,63}$")
_PASS_RE = re.compile(r"^lpass_[0-9a-f]{32}$")
_WORKER_RE = _CONSUMER_RE
_TOKEN_RE = re.compile(r"^llease_[0-9a-f]{32}$")
_OPERATION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}$")

_MATERIALIZE_SOURCE_ROWS_SQL = """
    WITH selected AS (
      SELECT page_id,change_generation,change_mask,source_exists
      FROM notion_page_change_watermarks
      WHERE page_id>?
      ORDER BY page_id LIMIT ?
    )
    SELECT s.*,p.id AS physical_page_id,p.archived,h.incarnation_id,
           h.incarnation_generation,h.retired_at
    FROM selected s
    LEFT JOIN pages p ON p.id=s.page_id
    JOIN notion_page_incarnation_history h ON h.page_id=s.page_id
      AND h.incarnation_generation=(
        SELECT MAX(h2.incarnation_generation)
        FROM notion_page_incarnation_history h2
        WHERE h2.page_id=s.page_id
      )
    ORDER BY s.page_id
"""


class LegacyChangeConsumerConflict(Exception):
    """An immutable operation id was reused for a different request."""


class LegacyChangeConsumerLeaseConflict(Exception):
    """The pass lease, fencing generation, or row version is stale."""


class LegacyChangeConsumerSourceFenceConflict(Exception):
    """The exact owner source no longer matches the pass snapshot."""


@dataclass(frozen=True)
class LegacyChangeConsumerCounters:
    scanned: int
    materialized: int
    duplicate: int

    def descriptor(self) -> dict[str, int]:
        return {
            "scanned": self.scanned,
            "materialized": self.materialized,
            "duplicate": self.duplicate,
        }


@dataclass(frozen=True)
class LegacyChangeConsumerWork:
    consumer: str
    pass_id: str
    source_owner_id: str
    page_id: str
    source_epoch: int
    full_sweep_generation: int
    source_device: int
    source_inode: int
    source_link_count: int
    change_generation: int
    change_mask: int
    source_exists: bool
    source_active: bool
    incarnation_id: str
    incarnation_generation: int
    incarnation_retired: bool
    receipt_sha256: str


@dataclass(frozen=True)
class LegacyChangeConsumerWorkBatch:
    items: tuple[LegacyChangeConsumerWork, ...]
    has_more: bool
    scanned_count: int


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_text(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _operation_request_sha256(
    *, consumer: str, action: str, request: dict[str, Any]
) -> str:
    return _sha256_text(
        _canonical_json(
            {
                "domain": "code_assist.notion.legacy_change_consumer.request.v1",
                "consumer": consumer,
                "action": action,
                "request": request,
            }
        )
    )


def _name(value: object, *, field: str, pattern: re.Pattern[str]) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise ValueError(f"valid {field} required")
    return value


def _integer(
    value: object, *, field: str, minimum: int = 1, maximum: int = MAX_SEQUENCE
) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not minimum <= value <= maximum
    ):
        raise ValueError(f"valid {field} required")
    return value


def _lease_token_digest(value: object) -> str:
    token = _name(value, field="lease_token", pattern=_TOKEN_RE)
    return _sha256_text("code_assist.notion.legacy_change_consumer.lease.v1\0" + token)


def _source_fence(value: LegacySourceFence) -> LegacySourceFence:
    if not isinstance(value, LegacySourceFence):
        raise ValueError("LegacySourceFence required")
    return LegacySourceFence(
        source_epoch=_integer(value.source_epoch, field="source_epoch"),
        full_sweep_generation=_integer(
            value.full_sweep_generation, field="full_sweep_generation"
        ),
        device=sqlite_source_identity(value.device),
        inode=sqlite_source_identity(value.inode),
        link_count=_integer(
            value.link_count, field="source_link_count", maximum=65_535
        ),
    )


class LegacyChangeConsumerStore:
    """One authenticated v3 source connection and exact owner identity."""

    def __init__(
        self,
        con: sqlite3.Connection,
        *,
        source_owner_id: str,
        source_fence: LegacySourceFence,
        trusted_clock: Callable[[], int] | None = None,
    ) -> None:
        if not isinstance(con, sqlite3.Connection) or con.in_transaction:
            raise ValueError("idle SQLite connection required")
        self._con = con
        self._con.row_factory = sqlite3.Row
        self._con.execute("PRAGMA foreign_keys=ON")
        self._owner = _name(
            source_owner_id,
            field="source_owner_id",
            pattern=re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{1,63}$"),
        )
        self._fence = _source_fence(source_fence)
        self._clock = trusted_clock or (lambda: int(time.time()))
        self._now_value = 0
        self._con.create_function(
            CONSUMER_WRITE_CONTEXT_UDF, 0, lambda: 1, deterministic=True
        )
        self._con.create_function(
            CONSUMER_NOW_UDF, 0, lambda: self._now_value, deterministic=False
        )
        inspect_legacy_change_consumer_contract(self._con, verify_integrity=True)

    def _now(self) -> int:
        value = self._clock()
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not 0 <= value <= MAX_TIMESTAMP
        ):
            raise LegacyChangeConsumerContractError(
                "trusted legacy change-consumer clock is invalid"
            )
        self._now_value = value
        return value

    def _assert_current_source(self, con: sqlite3.Connection) -> None:
        state = con.execute(
            "SELECT source_epoch,full_sweep_generation "
            "FROM notion_legacy_change_state WHERE singleton=1"
        ).fetchone()
        if (
            not state
            or int(state["source_epoch"]) != self._fence.source_epoch
            or int(state["full_sweep_generation"])
            != self._fence.full_sweep_generation
        ):
            raise LegacyChangeConsumerSourceFenceConflict(
                "legacy change-consumer source generation changed"
            )

    def assert_current_source(self) -> None:
        """Fail before catalog I/O when the materialized source fence drifted."""
        if self._con.in_transaction:
            raise ValueError("idle SQLite connection required")
        self._con.execute("BEGIN")
        try:
            self._assert_current_source(self._con)
        finally:
            self._con.rollback()

    @staticmethod
    def _pass_public(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "pass_id": str(row["pass_id"]),
            "status": str(row["status"]),
            "has_more": bool(row["has_more"]),
            "full_sweep_required": bool(row["full_sweep_required"]),
            "fencing_generation": int(row["fencing_generation"]),
            "version": int(row["row_version"]),
            "counters": {
                "scanned": int(row["scanned_count"]),
                "materialized": int(row["materialized_count"]),
                "duplicate": int(row["duplicate_count"]),
            },
        }

    @classmethod
    def _validate_result(cls, action: str, value: Any) -> dict[str, Any]:
        if action == FULL_SWEEP_COMPLETION_ACTION:
            return validate_full_sweep_completion_result(value)
        if action == FULL_SWEEP_SET_COMPLETION_ACTION:
            return validate_full_sweep_set_completion_result(value)
        if action == "legacy.change_consumer.work_effect_recorded":
            if not isinstance(value, dict) or set(value) != {"pass", "delta"}:
                raise LegacyChangeConsumerContractError(
                    "legacy change-consumer operation ledger is invalid"
                )
            passed = value.get("pass")
            delta = value.get("delta")
            if (
                not isinstance(passed, dict)
                or set(passed)
                != {
                    "pass_id",
                    "status",
                    "has_more",
                    "full_sweep_required",
                    "fencing_generation",
                    "version",
                    "counters",
                }
                or passed.get("status") != "materialized"
                or passed.get("has_more") is not False
                or not isinstance(passed.get("full_sweep_required"), bool)
                or not isinstance(delta, dict)
                or set(delta) != {"applied", "ignored", "superseded"}
            ):
                raise LegacyChangeConsumerContractError(
                    "legacy change-consumer operation ledger is invalid"
                )
            _name(passed.get("pass_id"), field="pass_id", pattern=_PASS_RE)
            _integer(
                passed.get("fencing_generation"), field="fencing_generation"
            )
            _integer(passed.get("version"), field="version")
            counters = passed.get("counters")
            if not isinstance(counters, dict) or set(counters) != {
                "scanned", "materialized", "duplicate"
            }:
                raise LegacyChangeConsumerContractError(
                    "legacy change-consumer operation ledger is invalid"
                )
            for field in ("scanned", "materialized", "duplicate"):
                _integer(
                    counters.get(field),
                    field=field,
                    minimum=0,
                    maximum=MAX_SEQUENCE,
                )
            if counters["materialized"] + counters["duplicate"] != counters["scanned"]:
                raise LegacyChangeConsumerContractError(
                    "legacy change-consumer operation ledger is invalid"
                )
            for field in ("applied", "ignored", "superseded"):
                _integer(delta.get(field), field=field, minimum=0, maximum=1)
            if sum(delta.values()) != 1:
                raise LegacyChangeConsumerContractError(
                    "legacy change-consumer operation ledger is invalid"
                )
            return value
        if not isinstance(value, dict) or set(value) != {"pass", "delta"}:
            raise LegacyChangeConsumerContractError(
                "legacy change-consumer operation ledger is invalid"
            )
        passed = value.get("pass")
        delta = value.get("delta")
        if (
            not isinstance(passed, dict)
            or set(passed)
            != {
                "pass_id",
                "status",
                "has_more",
                "full_sweep_required",
                "fencing_generation",
                "version",
                "counters",
            }
            or not isinstance(delta, dict)
            or set(delta) != {"scanned", "materialized", "duplicate"}
            or passed.get("status") not in {"running", "materialized"}
            or not isinstance(passed.get("has_more"), bool)
            or not isinstance(passed.get("full_sweep_required"), bool)
            or (passed.get("status") == "running") is not passed.get("has_more")
        ):
            raise LegacyChangeConsumerContractError(
                "legacy change-consumer operation ledger is invalid"
            )
        _name(passed.get("pass_id"), field="pass_id", pattern=_PASS_RE)
        _integer(
            passed.get("fencing_generation"), field="fencing_generation"
        )
        _integer(passed.get("version"), field="version")
        counters = passed.get("counters")
        if not isinstance(counters, dict) or set(counters) != set(delta):
            raise LegacyChangeConsumerContractError(
                "legacy change-consumer operation ledger is invalid"
            )
        for mapping in (counters, delta):
            for field in ("scanned", "materialized", "duplicate"):
                _integer(
                    mapping.get(field),
                    field=field,
                    minimum=0,
                    maximum=MAX_SEQUENCE,
                )
            if mapping["materialized"] + mapping["duplicate"] != mapping["scanned"]:
                raise LegacyChangeConsumerContractError(
                    "legacy change-consumer operation ledger is invalid"
                )
        if action in {
            "legacy.change_consumer.pass_started",
            "legacy.change_consumer.pass_reclaimed",
        } and any(delta.values()):
            raise LegacyChangeConsumerContractError(
                "legacy change-consumer operation ledger is invalid"
            )
        if action == "legacy.change_consumer.pass_started" and (
            passed["status"] != "running"
            or passed["fencing_generation"] != 1
            or passed["version"] != 1
            or any(counters.values())
        ):
            raise LegacyChangeConsumerContractError(
                "legacy change-consumer operation ledger is invalid"
            )
        if action == "legacy.change_consumer.pass_reclaimed" and (
            passed["status"] != "running"
            or passed["fencing_generation"] < 2
            or passed["version"] < 2
        ):
            raise LegacyChangeConsumerContractError(
                "legacy change-consumer operation ledger is invalid"
            )
        if action == "legacy.change_consumer.batch_materialized" and (
            passed["status"] == "running" and delta["scanned"] == 0
        ):
            raise LegacyChangeConsumerContractError(
                "legacy change-consumer operation ledger is invalid"
            )
        return value

    @staticmethod
    def _full_sweep_operation_id(pass_id: str) -> str:
        return FULL_SWEEP_OPERATION_PREFIX + pass_id

    @staticmethod
    def _full_sweep_set_operation_id(pass_id: str) -> str:
        return FULL_SWEEP_SET_OPERATION_PREFIX + pass_id

    @staticmethod
    def _terminal_workspaces(
        con: sqlite3.Connection, *, consumer: str, pass_id: str
    ) -> tuple[set[str], int]:
        workspaces = {
            str(row[0])
            for row in con.execute(
                "SELECT DISTINCT effect_workspace_id "
                "FROM notion_legacy_change_consumer_work "
                "WHERE consumer=? AND pass_id=? "
                "AND status IN ('applied','superseded')",
                (consumer, pass_id),
            ).fetchall()
        }
        non_acknowledgeable = int(
            con.execute(
                "SELECT COUNT(*) FROM notion_legacy_change_consumer_work "
                "WHERE consumer=? AND pass_id=? "
                "AND status IN ('ignored','superseded')",
                (consumer, pass_id),
            ).fetchone()[0]
        )
        return workspaces, non_acknowledgeable

    @classmethod
    def _full_sweep_result_from_ledger(
        cls,
        con: sqlite3.Connection,
        *,
        consumer: str,
        pass_id: str,
    ) -> dict[str, Any] | None:
        row = con.execute(
            "SELECT action,request_sha256,result_json FROM "
            "notion_legacy_change_consumer_operations "
            "WHERE consumer=? AND operation_id=?",
            (consumer, cls._full_sweep_operation_id(pass_id)),
        ).fetchone()
        if not row:
            return None
        if str(row["action"]) != FULL_SWEEP_COMPLETION_ACTION:
            raise LegacyChangeConsumerContractError(
                "legacy change consumer completion evidence is invalid"
            )
        try:
            result = json.loads(str(row["result_json"]))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise LegacyChangeConsumerContractError(
                "legacy change consumer completion evidence is invalid"
            ) from exc
        if str(row["result_json"]) != _canonical_json(result):
            raise LegacyChangeConsumerContractError(
                "legacy change consumer completion evidence is invalid"
            )
        validated = validate_full_sweep_completion_result(result)
        if validated["pass_id"] != pass_id:
            raise LegacyChangeConsumerContractError(
                "legacy change consumer completion evidence is invalid"
            )
        passed = con.execute(
            "SELECT * FROM notion_legacy_change_consumer_passes "
            "WHERE consumer=? AND pass_id=?",
            (consumer, pass_id),
        ).fetchone()
        pending = con.execute(
            "SELECT COUNT(*) FROM notion_legacy_change_consumer_work "
            "WHERE consumer=? AND pass_id=? AND status='pending'",
            (consumer, pass_id),
        ).fetchone()
        if not passed:
            raise LegacyChangeConsumerContractError(
                "legacy change consumer completion evidence is invalid"
            )
        expected_fence = {
            "source_epoch": int(passed["source_epoch"]),
            "full_sweep_generation": int(passed["full_sweep_generation"]),
            "source_device": int(passed["source_device"]),
            "source_inode": int(passed["source_inode"]),
            "source_link_count": int(passed["source_link_count"]),
        }
        expected_request = {
            "pass_id": pass_id,
            "source_owner_id": str(passed["source_owner_id"]),
            "source_fence": expected_fence,
            "catalog_completion": validated["catalog_completion"],
        }
        workspaces, non_acknowledgeable = cls._terminal_workspaces(
            con, consumer=consumer, pass_id=pass_id
        )
        if (
            str(passed["status"]) != "materialized"
            or not bool(passed["full_sweep_required"])
            or str(passed["source_owner_id"])
            != str(validated["source_owner_id"])
            or expected_fence != validated["source_fence"]
            or int(pending[0] if pending else -1) != 0
            or non_acknowledgeable != 0
            or (
                bool(workspaces)
                and workspaces
                != {str(validated["catalog_completion"]["workspace_id"])}
            )
            or str(row["request_sha256"])
            != _operation_request_sha256(
                consumer=consumer,
                action=FULL_SWEEP_COMPLETION_ACTION,
                request=expected_request,
            )
        ):
            raise LegacyChangeConsumerContractError(
                "legacy change consumer completion evidence is invalid"
            )
        return validated

    @classmethod
    def _full_sweep_set_result_from_ledger(
        cls,
        con: sqlite3.Connection,
        *,
        consumer: str,
        pass_id: str,
    ) -> dict[str, Any] | None:
        row = con.execute(
            "SELECT action,request_sha256,result_json FROM "
            "notion_legacy_change_consumer_operations "
            "WHERE consumer=? AND operation_id=?",
            (consumer, cls._full_sweep_set_operation_id(pass_id)),
        ).fetchone()
        if not row:
            return None
        if str(row["action"]) != FULL_SWEEP_SET_COMPLETION_ACTION:
            raise LegacyChangeConsumerContractError(
                "legacy change consumer completion evidence is invalid"
            )
        try:
            result = json.loads(str(row["result_json"]))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise LegacyChangeConsumerContractError(
                "legacy change consumer completion evidence is invalid"
            ) from exc
        if str(row["result_json"]) != _canonical_json(result):
            raise LegacyChangeConsumerContractError(
                "legacy change consumer completion evidence is invalid"
            )
        validated = validate_full_sweep_set_completion_result(result)
        if validated["pass_id"] != pass_id:
            raise LegacyChangeConsumerContractError(
                "legacy change consumer completion evidence is invalid"
            )
        passed = con.execute(
            "SELECT * FROM notion_legacy_change_consumer_passes "
            "WHERE consumer=? AND pass_id=?",
            (consumer, pass_id),
        ).fetchone()
        pending = con.execute(
            "SELECT COUNT(*) FROM notion_legacy_change_consumer_work "
            "WHERE consumer=? AND pass_id=? AND status='pending'",
            (consumer, pass_id),
        ).fetchone()
        if not passed:
            raise LegacyChangeConsumerContractError(
                "legacy change consumer completion evidence is invalid"
            )
        expected_fence = {
            "source_epoch": int(passed["source_epoch"]),
            "full_sweep_generation": int(passed["full_sweep_generation"]),
            "source_device": int(passed["source_device"]),
            "source_inode": int(passed["source_inode"]),
            "source_link_count": int(passed["source_link_count"]),
        }
        expected_request = {
            "pass_id": pass_id,
            "source_owner_id": str(passed["source_owner_id"]),
            "source_fence": expected_fence,
            "catalog_completions": validated["catalog_completions"],
        }
        completion_workspaces = {
            str(item["workspace_id"])
            for item in validated["catalog_completions"]
        }
        workspaces, non_acknowledgeable = cls._terminal_workspaces(
            con, consumer=consumer, pass_id=pass_id
        )
        if (
            str(passed["status"]) != "materialized"
            or not bool(passed["full_sweep_required"])
            or str(passed["source_owner_id"])
            != str(validated["source_owner_id"])
            or expected_fence != validated["source_fence"]
            or int(pending[0] if pending else -1) != 0
            or non_acknowledgeable != 0
            or (bool(workspaces) and workspaces != completion_workspaces)
            or str(row["request_sha256"])
            != _operation_request_sha256(
                consumer=consumer,
                action=FULL_SWEEP_SET_COMPLETION_ACTION,
                request=expected_request,
            )
        ):
            raise LegacyChangeConsumerContractError(
                "legacy change consumer completion evidence is invalid"
            )
        return validated

    @classmethod
    def _any_full_sweep_result_from_ledger(
        cls,
        con: sqlite3.Connection,
        *,
        consumer: str,
        pass_id: str,
    ) -> dict[str, Any] | None:
        multiple = cls._full_sweep_set_result_from_ledger(
            con, consumer=consumer, pass_id=pass_id
        )
        single = cls._full_sweep_result_from_ledger(
            con, consumer=consumer, pass_id=pass_id
        )
        if multiple is not None and single is not None:
            raise LegacyChangeConsumerContractError(
                "legacy change consumer completion evidence is invalid"
            )
        return multiple if multiple is not None else single

    def _mutate(
        self,
        *,
        consumer: str,
        operation_id: str,
        action: str,
        request: dict[str, Any],
        handler: Callable[[sqlite3.Connection, int], dict[str, Any]],
    ) -> dict[str, Any]:
        request_sha256 = _operation_request_sha256(
            consumer=consumer,
            action=action,
            request=request,
        )
        now = self._now()
        con = self._con
        con.execute("BEGIN IMMEDIATE")
        try:
            previous = con.execute(
                "SELECT action,request_sha256,result_json "
                "FROM notion_legacy_change_consumer_operations "
                "WHERE consumer=? AND operation_id=?",
                (consumer, operation_id),
            ).fetchone()
            if previous:
                if (
                    str(previous["action"]) != action
                    or str(previous["request_sha256"]) != request_sha256
                ):
                    raise LegacyChangeConsumerConflict(operation_id)
                try:
                    result = json.loads(str(previous["result_json"]))
                except (TypeError, ValueError, json.JSONDecodeError) as exc:
                    raise LegacyChangeConsumerContractError(
                        "legacy change-consumer operation ledger is invalid"
                    ) from exc
                con.rollback()
                return self._validate_result(action, result)
            result = self._validate_result(action, handler(con, now))
            con.execute(
                """
                INSERT INTO notion_legacy_change_consumer_operations(
                  consumer,operation_id,action,request_sha256,result_json,created_at
                ) VALUES(?,?,?,?,?,?)
                """,
                (
                    consumer,
                    operation_id,
                    action,
                    request_sha256,
                    _canonical_json(result),
                    now,
                ),
            )
            con.commit()
            return result
        except Exception:
            con.rollback()
            raise

    def start_pass(
        self,
        *,
        consumer: str,
        pass_id: str,
        worker: str,
        lease_token: str,
        lease_seconds: int,
        operation_id: str,
    ) -> dict[str, Any]:
        consumer_name = _name(consumer, field="consumer", pattern=_CONSUMER_RE)
        normalized_pass = _name(pass_id, field="pass_id", pattern=_PASS_RE)
        worker_name = _name(worker, field="worker", pattern=_WORKER_RE)
        token_sha256 = _lease_token_digest(lease_token)
        lease = _integer(
            lease_seconds,
            field="lease_seconds",
            maximum=MAX_LEASE_SECONDS,
        )
        op_id = _name(operation_id, field="operation_id", pattern=_OPERATION_RE)
        request = {
            "pass_id": normalized_pass,
            "source_owner_id": self._owner,
            "source_fence": {
                "source_epoch": self._fence.source_epoch,
                "full_sweep_generation": self._fence.full_sweep_generation,
                "source_device": self._fence.device,
                "source_inode": self._fence.inode,
                "source_link_count": self._fence.link_count,
            },
            "worker": worker_name,
            "lease_token_sha256": token_sha256,
            "lease_seconds": lease,
        }

        def handler(con: sqlite3.Connection, now: int) -> dict[str, Any]:
            if now > MAX_TIMESTAMP - lease:
                raise LegacyChangeConsumerContractError(
                    "trusted legacy change-consumer clock is invalid"
                )
            self._assert_current_source(con)
            previous = con.execute(
                "SELECT pass_id,full_sweep_generation,full_sweep_required "
                "FROM notion_legacy_change_consumer_passes "
                "WHERE consumer=? AND status='materialized' "
                "ORDER BY started_at DESC,pass_id DESC LIMIT 1",
                (consumer_name,),
            ).fetchone()
            if (
                previous is not None
                and bool(previous["full_sweep_required"])
                and self._any_full_sweep_result_from_ledger(
                    con,
                    consumer=consumer_name,
                    pass_id=str(previous["pass_id"]),
                )
                is None
            ):
                raise LegacyChangeConsumerConflict(
                    "required full sweep is not completed"
                )
            full_sweep_required = (
                previous is None
                or int(previous["full_sweep_generation"])
                != self._fence.full_sweep_generation
            )
            try:
                con.execute(
                    """
                    INSERT INTO notion_legacy_change_consumer_passes(
                      consumer,pass_id,source_owner_id,status,source_epoch,
                      full_sweep_generation,source_device,source_inode,
                      source_link_count,full_sweep_required,cursor_page_id,has_more,
                      scanned_count,materialized_count,duplicate_count,
                      lease_worker,lease_token_sha256,lease_expires_at,
                      fencing_generation,row_version,failure_code,started_at,
                      updated_at,materialized_at,failed_at
                    ) VALUES(?,?,?,'running',?,?,?,?,?,?,NULL,1,0,0,0,?,?,?,1,1,
                             NULL,?,?,NULL,NULL)
                    """,
                    (
                        consumer_name,
                        normalized_pass,
                        self._owner,
                        self._fence.source_epoch,
                        self._fence.full_sweep_generation,
                        self._fence.device,
                        self._fence.inode,
                        self._fence.link_count,
                        1 if full_sweep_required else 0,
                        worker_name,
                        token_sha256,
                        now + lease,
                        now,
                        now,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise LegacyChangeConsumerLeaseConflict(
                    "legacy change-consumer pass unavailable"
                ) from exc
            row = con.execute(
                "SELECT * FROM notion_legacy_change_consumer_passes "
                "WHERE consumer=? AND pass_id=?",
                (consumer_name, normalized_pass),
            ).fetchone()
            return {
                "pass": self._pass_public(row),
                "delta": LegacyChangeConsumerCounters(0, 0, 0).descriptor(),
            }

        return self._mutate(
            consumer=consumer_name,
            operation_id=op_id,
            action="legacy.change_consumer.pass_started",
            request=request,
            handler=handler,
        )

    def reclaim_pass(
        self,
        *,
        consumer: str,
        pass_id: str,
        worker: str,
        lease_token: str,
        lease_seconds: int,
        expected_fencing_generation: int,
        expected_version: int,
        operation_id: str,
    ) -> dict[str, Any]:
        """Reclaim one expired exact-source pass without moving its cursor."""
        consumer_name = _name(consumer, field="consumer", pattern=_CONSUMER_RE)
        normalized_pass = _name(pass_id, field="pass_id", pattern=_PASS_RE)
        worker_name = _name(worker, field="worker", pattern=_WORKER_RE)
        token_sha256 = _lease_token_digest(lease_token)
        lease = _integer(
            lease_seconds,
            field="lease_seconds",
            maximum=MAX_LEASE_SECONDS,
        )
        fence_generation = _integer(
            expected_fencing_generation, field="expected_fencing_generation"
        )
        version = _integer(expected_version, field="expected_version")
        op_id = _name(operation_id, field="operation_id", pattern=_OPERATION_RE)
        request = {
            "pass_id": normalized_pass,
            "worker": worker_name,
            "lease_token_sha256": token_sha256,
            "lease_seconds": lease,
            "expected_fencing_generation": fence_generation,
            "expected_version": version,
        }

        def handler(con: sqlite3.Connection, now: int) -> dict[str, Any]:
            if now > MAX_TIMESTAMP - lease:
                raise LegacyChangeConsumerContractError(
                    "trusted legacy change-consumer clock is invalid"
                )
            row = con.execute(
                "SELECT * FROM notion_legacy_change_consumer_passes "
                "WHERE consumer=? AND pass_id=?",
                (consumer_name, normalized_pass),
            ).fetchone()
            if (
                not row
                or str(row["status"]) != "running"
                or str(row["source_owner_id"]) != self._owner
                or int(row["source_epoch"]) != self._fence.source_epoch
                or int(row["full_sweep_generation"])
                != self._fence.full_sweep_generation
                or int(row["source_device"]) != self._fence.device
                or int(row["source_inode"]) != self._fence.inode
                or int(row["source_link_count"]) != self._fence.link_count
                or int(row["fencing_generation"]) != fence_generation
                or int(row["row_version"]) != version
                or int(row["lease_expires_at"] or 0) > now
                or str(row["lease_token_sha256"] or "") == token_sha256
            ):
                raise LegacyChangeConsumerLeaseConflict(
                    "legacy change-consumer lease unavailable"
                )
            self._assert_current_source(con)
            updated = con.execute(
                """
                UPDATE notion_legacy_change_consumer_passes
                SET lease_worker=?,lease_token_sha256=?,lease_expires_at=?,
                    fencing_generation=fencing_generation+1,
                    row_version=row_version+1,updated_at=?
                WHERE consumer=? AND pass_id=? AND status='running'
                  AND row_version=? AND fencing_generation=?
                  AND lease_expires_at<=?
                """,
                (
                    worker_name,
                    token_sha256,
                    now + lease,
                    now,
                    consumer_name,
                    normalized_pass,
                    version,
                    fence_generation,
                    now,
                ),
            )
            if updated.rowcount != 1:
                raise LegacyChangeConsumerLeaseConflict(
                    "legacy change-consumer lease unavailable"
                )
            current = con.execute(
                "SELECT * FROM notion_legacy_change_consumer_passes "
                "WHERE consumer=? AND pass_id=?",
                (consumer_name, normalized_pass),
            ).fetchone()
            return {
                "pass": self._pass_public(current),
                "delta": LegacyChangeConsumerCounters(0, 0, 0).descriptor(),
            }

        return self._mutate(
            consumer=consumer_name,
            operation_id=op_id,
            action="legacy.change_consumer.pass_reclaimed",
            request=request,
            handler=handler,
        )

    def materialize_next_batch(
        self,
        *,
        consumer: str,
        pass_id: str,
        worker: str,
        lease_token: str,
        fencing_generation: int,
        expected_version: int,
        limit: int,
        operation_id: str,
    ) -> dict[str, Any]:
        consumer_name = _name(consumer, field="consumer", pattern=_CONSUMER_RE)
        normalized_pass = _name(pass_id, field="pass_id", pattern=_PASS_RE)
        worker_name = _name(worker, field="worker", pattern=_WORKER_RE)
        token_sha256 = _lease_token_digest(lease_token)
        fence_generation = _integer(
            fencing_generation, field="fencing_generation"
        )
        version = _integer(expected_version, field="expected_version")
        batch_limit = _integer(
            limit, field="limit", maximum=MAX_BATCH_ROWS
        )
        op_id = _name(operation_id, field="operation_id", pattern=_OPERATION_RE)
        request = {
            "pass_id": normalized_pass,
            "worker": worker_name,
            "lease_token_sha256": token_sha256,
            "fencing_generation": fence_generation,
            "expected_version": version,
            "limit": batch_limit,
        }

        def handler(con: sqlite3.Connection, now: int) -> dict[str, Any]:
            row = con.execute(
                "SELECT * FROM notion_legacy_change_consumer_passes "
                "WHERE consumer=? AND pass_id=?",
                (consumer_name, normalized_pass),
            ).fetchone()
            if (
                not row
                or str(row["status"]) != "running"
                or str(row["source_owner_id"]) != self._owner
                or int(row["source_epoch"]) != self._fence.source_epoch
                or int(row["full_sweep_generation"])
                != self._fence.full_sweep_generation
                or int(row["source_device"]) != self._fence.device
                or int(row["source_inode"]) != self._fence.inode
                or int(row["source_link_count"]) != self._fence.link_count
                or str(row["lease_worker"] or "") != worker_name
                or str(row["lease_token_sha256"] or "") != token_sha256
                or int(row["lease_expires_at"] or 0) <= now
                or int(row["fencing_generation"]) != fence_generation
                or int(row["row_version"]) != version
            ):
                raise LegacyChangeConsumerLeaseConflict(
                    "legacy change-consumer lease unavailable"
                )
            self._assert_current_source(con)
            after = str(row["cursor_page_id"] or "")
            source_rows = con.execute(
                _MATERIALIZE_SOURCE_ROWS_SQL,
                (after, batch_limit + 1),
            ).fetchall()
            has_more = len(source_rows) > batch_limit
            chosen = source_rows[:batch_limit]
            materialized = 0
            duplicate = 0
            for item in chosen:
                physical_exists = item["physical_page_id"] is not None
                source_active = bool(
                    physical_exists and int(item["archived"] or 0) == 0
                )
                if bool(item["source_exists"]) != source_active:
                    raise LegacyChangeConsumerSourceFenceConflict(
                        "legacy change-consumer receipt is inconsistent"
                    )
                incarnation_retired = item["retired_at"] is not None
                if incarnation_retired == physical_exists:
                    raise LegacyChangeConsumerSourceFenceConflict(
                        "legacy change-consumer receipt is inconsistent"
                    )
                receipt_sha256 = legacy_incarnation_receipt_sha256(
                    source_owner_id=self._owner,
                    page_id=str(item["page_id"]),
                    incarnation_id=str(item["incarnation_id"]),
                    incarnation_generation=int(item["incarnation_generation"]),
                    incarnation_retired=incarnation_retired,
                    change_generation=int(item["change_generation"]),
                    change_mask=int(item["change_mask"]),
                    source_exists=physical_exists,
                    source_active=source_active,
                    source_epoch=self._fence.source_epoch,
                    full_sweep_generation=self._fence.full_sweep_generation,
                    source_device=self._fence.device,
                    source_inode=self._fence.inode,
                    source_link_count=self._fence.link_count,
                )
                inserted = con.execute(
                    """
                    INSERT OR IGNORE INTO notion_legacy_change_consumer_work(
                      consumer,pass_id,source_owner_id,page_id,source_epoch,
                      full_sweep_generation,change_generation,change_mask,
                      source_exists,source_active,incarnation_id,
                      incarnation_generation,incarnation_retired,receipt_sha256,
                      status,effect_workspace_id,effect_binding_generation,
                      effect_operation_id,effect_disposition,row_version,
                      created_at,applied_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,'pending',NULL,NULL,
                             NULL,NULL,1,?,NULL)
                    """,
                    (
                        consumer_name,
                        normalized_pass,
                        self._owner,
                        str(item["page_id"]),
                        self._fence.source_epoch,
                        self._fence.full_sweep_generation,
                        int(item["change_generation"]),
                        int(item["change_mask"]),
                        1 if physical_exists else 0,
                        1 if source_active else 0,
                        str(item["incarnation_id"]),
                        int(item["incarnation_generation"]),
                        1 if incarnation_retired else 0,
                        receipt_sha256,
                        now,
                    ),
                )
                if inserted.rowcount == 1:
                    materialized += 1
                else:
                    previous = con.execute(
                        """
                        SELECT source_owner_id,incarnation_id,
                               incarnation_generation,receipt_sha256
                        FROM notion_legacy_change_consumer_work
                        WHERE consumer=? AND source_epoch=? AND page_id=?
                          AND change_generation=?
                        """,
                        (
                            consumer_name,
                            self._fence.source_epoch,
                            str(item["page_id"]),
                            int(item["change_generation"]),
                        ),
                    ).fetchone()
                    if (
                        not previous
                        or str(previous["source_owner_id"]) != self._owner
                        or str(previous["incarnation_id"])
                        != str(item["incarnation_id"])
                        or int(previous["incarnation_generation"])
                        != int(item["incarnation_generation"])
                        or str(previous["receipt_sha256"]) != receipt_sha256
                    ):
                        raise LegacyChangeConsumerSourceFenceConflict(
                            "legacy change-consumer receipt identity changed"
                        )
                    duplicate += 1
            scanned = len(chosen)
            next_cursor = str(chosen[-1]["page_id"]) if chosen else after
            status = "running" if has_more else "materialized"
            updated = con.execute(
                """
                UPDATE notion_legacy_change_consumer_passes
                SET status=?,cursor_page_id=CASE WHEN ?>0 THEN ? ELSE cursor_page_id END,
                    has_more=?,scanned_count=scanned_count+?,
                    materialized_count=materialized_count+?,
                    duplicate_count=duplicate_count+?,
                    lease_worker=CASE WHEN ?=1 THEN lease_worker ELSE NULL END,
                    lease_token_sha256=CASE WHEN ?=1 THEN lease_token_sha256 ELSE NULL END,
                    lease_expires_at=CASE WHEN ?=1 THEN lease_expires_at ELSE NULL END,
                    row_version=row_version+1,updated_at=?,
                    materialized_at=CASE WHEN ?=1 THEN NULL ELSE ? END
                WHERE consumer=? AND pass_id=? AND status='running'
                  AND row_version=? AND fencing_generation=?
                  AND lease_worker=? AND lease_token_sha256=? AND lease_expires_at>?
                """,
                (
                    status,
                    scanned,
                    next_cursor,
                    1 if has_more else 0,
                    scanned,
                    materialized,
                    duplicate,
                    1 if has_more else 0,
                    1 if has_more else 0,
                    1 if has_more else 0,
                    now,
                    1 if has_more else 0,
                    now,
                    consumer_name,
                    normalized_pass,
                    version,
                    fence_generation,
                    worker_name,
                    token_sha256,
                    now,
                ),
            )
            if updated.rowcount != 1:
                raise LegacyChangeConsumerLeaseConflict(
                    "legacy change-consumer lease unavailable"
                )
            current = con.execute(
                "SELECT * FROM notion_legacy_change_consumer_passes "
                "WHERE consumer=? AND pass_id=?",
                (consumer_name, normalized_pass),
            ).fetchone()
            return {
                "pass": self._pass_public(current),
                "delta": LegacyChangeConsumerCounters(
                    scanned, materialized, duplicate
                ).descriptor(),
            }

        return self._mutate(
            consumer=consumer_name,
            operation_id=op_id,
            action="legacy.change_consumer.batch_materialized",
            request=request,
            handler=handler,
        )

    def pending_work_count(self, *, consumer: str, pass_id: str) -> int:
        return self.pass_status(consumer=consumer, pass_id=pass_id)["work"][
            "pending"
        ]

    def pass_status(self, *, consumer: str, pass_id: str) -> dict[str, Any]:
        """Return identifier-free pass/work counters for offline lag evidence."""
        consumer_name = _name(consumer, field="consumer", pattern=_CONSUMER_RE)
        normalized_pass = _name(pass_id, field="pass_id", pattern=_PASS_RE)
        passed = self._con.execute(
            "SELECT * FROM notion_legacy_change_consumer_passes "
            "WHERE consumer=? AND pass_id=? AND source_owner_id=?",
            (consumer_name, normalized_pass, self._owner),
        ).fetchone()
        if not passed:
            raise LegacyChangeConsumerConflict("legacy change-consumer pass unavailable")
        counts = {status: 0 for status in ("pending", "applied", "ignored", "superseded")}
        for row in self._con.execute(
            "SELECT status,COUNT(*) AS n FROM notion_legacy_change_consumer_work "
            "WHERE consumer=? AND pass_id=? GROUP BY status",
            (consumer_name, normalized_pass),
        ).fetchall():
            counts[str(row["status"])] = int(row["n"])
        completion = self._any_full_sweep_result_from_ledger(
            self._con,
            consumer=consumer_name,
            pass_id=normalized_pass,
        )
        return {
            "pass": self._pass_public(passed),
            "work": counts,
            "full_sweep_completed": completion is not None,
        }

    def lag_report(self, *, consumer: str, pass_id: str) -> dict[str, Any]:
        """Return identifier-free durable lag evidence for one opaque pass."""
        status = self.pass_status(consumer=consumer, pass_id=pass_id)
        passed = status["pass"]
        work = dict(status["work"])
        full_sweep_required = bool(passed["full_sweep_required"])
        full_sweep_completed = bool(status["full_sweep_completed"])
        return {
            "pass_status": str(passed["status"]),
            "full_sweep_required": full_sweep_required,
            "full_sweep_completed": full_sweep_completed,
            "work": work,
            "lagging": (
                work["pending"] > 0
                or work["ignored"] > 0
                or work["superseded"] > 0
                or (full_sweep_required and not full_sweep_completed)
            ),
        }

    def pending_work_batch(
        self,
        *,
        consumer: str,
        pass_id: str,
        limit: int = MAX_BATCH_ROWS,
    ) -> LegacyChangeConsumerWorkBatch:
        """Read the smallest bounded pending set; terminal rows disappear.

        No persistent page-id cursor is needed: a crash leaves the row pending,
        while a committed terminal transition removes it from the next query.
        This also prevents a newly materialized smaller page id from being
        skipped by a stale cursor.
        """
        consumer_name = _name(consumer, field="consumer", pattern=_CONSUMER_RE)
        normalized_pass = _name(pass_id, field="pass_id", pattern=_PASS_RE)
        bounded = _integer(limit, field="limit", maximum=MAX_BATCH_ROWS)
        passed = self._con.execute(
            "SELECT * FROM notion_legacy_change_consumer_passes "
            "WHERE consumer=? AND pass_id=?",
            (consumer_name, normalized_pass),
        ).fetchone()
        if (
            not passed
            or str(passed["status"]) != "materialized"
            or str(passed["source_owner_id"]) != self._owner
        ):
            raise LegacyChangeConsumerConflict(
                "legacy change-consumer pass is not materialized"
            )
        rows = self._con.execute(
            "SELECT * FROM notion_legacy_change_consumer_work "
            "WHERE consumer=? AND pass_id=? AND status='pending' "
            "ORDER BY page_id LIMIT ?",
            (consumer_name, normalized_pass, bounded + 1),
        ).fetchall()
        window = rows[:bounded]
        items = tuple(self._work_from_row(passed, row) for row in window)
        return LegacyChangeConsumerWorkBatch(
            items=items,
            has_more=len(rows) > bounded,
            scanned_count=len(items),
        )

    @staticmethod
    def _work_from_row(
        passed: sqlite3.Row, row: sqlite3.Row
    ) -> LegacyChangeConsumerWork:
        return LegacyChangeConsumerWork(
                consumer=str(row["consumer"]),
                pass_id=str(row["pass_id"]),
                source_owner_id=str(row["source_owner_id"]),
                page_id=str(row["page_id"]),
                source_epoch=int(row["source_epoch"]),
                full_sweep_generation=int(row["full_sweep_generation"]),
                source_device=int(passed["source_device"]),
                source_inode=int(passed["source_inode"]),
                source_link_count=int(passed["source_link_count"]),
                change_generation=int(row["change_generation"]),
                change_mask=int(row["change_mask"]),
                source_exists=bool(row["source_exists"]),
                source_active=bool(row["source_active"]),
                incarnation_id=str(row["incarnation_id"]),
                incarnation_generation=int(row["incarnation_generation"]),
                incarnation_retired=bool(row["incarnation_retired"]),
                receipt_sha256=str(row["receipt_sha256"]),
            )

    def pending_work_for_page(
        self,
        *,
        consumer: str,
        pass_id: str,
        page_id: str,
    ) -> LegacyChangeConsumerWork | None:
        consumer_name = _name(consumer, field="consumer", pattern=_CONSUMER_RE)
        normalized_pass = _name(pass_id, field="pass_id", pattern=_PASS_RE)
        normalized_page = str(page_id or "")
        if (
            not normalized_page
            or normalized_page != normalized_page.strip()
            or len(normalized_page) > 160
        ):
            raise ValueError("valid page_id required")
        passed = self._con.execute(
            "SELECT * FROM notion_legacy_change_consumer_passes "
            "WHERE consumer=? AND pass_id=? AND source_owner_id=?",
            (consumer_name, normalized_pass, self._owner),
        ).fetchone()
        if not passed or str(passed["status"]) != "materialized":
            raise LegacyChangeConsumerConflict(
                "legacy change-consumer pass is not materialized"
            )
        row = self._con.execute(
            "SELECT * FROM notion_legacy_change_consumer_work "
            "WHERE consumer=? AND pass_id=? AND page_id=? AND status='pending'",
            (consumer_name, normalized_pass, normalized_page),
        ).fetchone()
        return self._work_from_row(passed, row) if row else None

    @staticmethod
    def _work_identity(work: LegacyChangeConsumerWork) -> dict[str, Any]:
        if not isinstance(work, LegacyChangeConsumerWork):
            raise ValueError("LegacyChangeConsumerWork required")
        return {
            "consumer": work.consumer,
            "pass_id": work.pass_id,
            "source_owner_id": work.source_owner_id,
            "page_id": work.page_id,
            "source_epoch": work.source_epoch,
            "full_sweep_generation": work.full_sweep_generation,
            "source_device": work.source_device,
            "source_inode": work.source_inode,
            "source_link_count": work.source_link_count,
            "change_generation": work.change_generation,
            "change_mask": work.change_mask,
            "source_exists": work.source_exists,
            "source_active": work.source_active,
            "incarnation_id": work.incarnation_id,
            "incarnation_generation": work.incarnation_generation,
            "incarnation_retired": work.incarnation_retired,
            "receipt_sha256": work.receipt_sha256,
        }

    def record_work_effect(
        self,
        *,
        work: LegacyChangeConsumerWork,
        terminal_status: str,
        effect_workspace_id: str | None,
        effect_binding_generation: int | None,
        effect_operation_id: str,
        effect_disposition: str,
        operation_id: str,
    ) -> dict[str, Any]:
        """Commit one exact catalog-effect receipt without acknowledging source."""
        identity = self._work_identity(work)
        consumer_name = _name(work.consumer, field="consumer", pattern=_CONSUMER_RE)
        normalized_pass = _name(work.pass_id, field="pass_id", pattern=_PASS_RE)
        if work.source_owner_id != self._owner:
            raise ValueError("work source owner does not match")
        if terminal_status not in {"applied", "ignored", "superseded"}:
            raise ValueError("valid terminal_status required")
        effect_op = _name(
            effect_operation_id, field="effect_operation_id", pattern=_OPERATION_RE
        )
        source_op = _name(operation_id, field="operation_id", pattern=_OPERATION_RE)
        if (
            not isinstance(effect_disposition, str)
            or re.fullmatch(r"[a-z][a-z0-9_.-]{1,63}", effect_disposition) is None
        ):
            raise ValueError("valid effect_disposition required")
        workspace = str(effect_workspace_id or "")
        binding_generation = effect_binding_generation
        if terminal_status == "ignored":
            if workspace or binding_generation is not None or effect_disposition != "no_binding":
                raise ValueError("ignored work must record no_binding")
            workspace_value: str | None = None
            binding_value: int | None = None
        else:
            if re.fullmatch(r"ws_[0-9a-f]{32}", workspace) is None:
                raise ValueError("valid effect_workspace_id required")
            workspace_value = workspace
            binding_value = _integer(
                binding_generation, field="effect_binding_generation"
            )
            if terminal_status == "superseded" and effect_disposition != "superseded":
                raise ValueError("superseded work disposition required")
            if terminal_status == "applied" and effect_disposition in {
                "no_binding", "superseded"
            }:
                raise ValueError("applied work disposition is invalid")
        request = {
            "work": identity,
            "terminal_status": terminal_status,
            "effect_workspace_id": workspace_value,
            "effect_binding_generation": binding_value,
            "effect_operation_id": effect_op,
            "effect_disposition": effect_disposition,
        }

        def handler(con: sqlite3.Connection, now: int) -> dict[str, Any]:
            passed = con.execute(
                "SELECT * FROM notion_legacy_change_consumer_passes "
                "WHERE consumer=? AND pass_id=?",
                (consumer_name, normalized_pass),
            ).fetchone()
            row = con.execute(
                "SELECT * FROM notion_legacy_change_consumer_work "
                "WHERE consumer=? AND pass_id=? AND page_id=?",
                (consumer_name, normalized_pass, work.page_id),
            ).fetchone()
            if not passed or str(passed["status"]) != "materialized" or not row:
                raise LegacyChangeConsumerConflict(
                    "legacy change-consumer work unavailable"
                )
            current_identity = {
                "consumer": str(row["consumer"]),
                "pass_id": str(row["pass_id"]),
                "source_owner_id": str(row["source_owner_id"]),
                "page_id": str(row["page_id"]),
                "source_epoch": int(row["source_epoch"]),
                "full_sweep_generation": int(row["full_sweep_generation"]),
                "source_device": int(passed["source_device"]),
                "source_inode": int(passed["source_inode"]),
                "source_link_count": int(passed["source_link_count"]),
                "change_generation": int(row["change_generation"]),
                "change_mask": int(row["change_mask"]),
                "source_exists": bool(row["source_exists"]),
                "source_active": bool(row["source_active"]),
                "incarnation_id": str(row["incarnation_id"]),
                "incarnation_generation": int(row["incarnation_generation"]),
                "incarnation_retired": bool(row["incarnation_retired"]),
                "receipt_sha256": str(row["receipt_sha256"]),
            }
            if current_identity != identity:
                raise LegacyChangeConsumerSourceFenceConflict(
                    "legacy change-consumer work identity changed"
                )
            if str(row["status"]) == "pending":
                updated = con.execute(
                    """
                    UPDATE notion_legacy_change_consumer_work
                    SET status=?,effect_workspace_id=?,
                        effect_binding_generation=?,effect_operation_id=?,
                        effect_disposition=?,row_version=row_version+1,
                        applied_at=?
                    WHERE consumer=? AND pass_id=? AND page_id=?
                      AND status='pending' AND row_version=1
                    """,
                    (
                        terminal_status,
                        workspace_value,
                        binding_value,
                        effect_op,
                        effect_disposition,
                        now,
                        consumer_name,
                        normalized_pass,
                        work.page_id,
                    ),
                )
                if updated.rowcount != 1:
                    raise LegacyChangeConsumerConflict(
                        "legacy change-consumer work unavailable"
                    )
            elif (
                str(row["status"]) != terminal_status
                or (str(row["effect_workspace_id"] or "") or None) != workspace_value
                or (
                    int(row["effect_binding_generation"])
                    if row["effect_binding_generation"] is not None
                    else None
                ) != binding_value
                or str(row["effect_operation_id"] or "") != effect_op
                or str(row["effect_disposition"] or "") != effect_disposition
            ):
                raise LegacyChangeConsumerConflict(
                    "legacy change-consumer work effect conflicts"
                )
            delta = {"applied": 0, "ignored": 0, "superseded": 0}
            delta[terminal_status] = 1
            return {"pass": self._pass_public(passed), "delta": delta}

        return self._mutate(
            consumer=consumer_name,
            operation_id=source_op,
            action="legacy.change_consumer.work_effect_recorded",
            request=request,
            handler=handler,
        )

    def get_full_sweep_completion_replay(
        self,
        *,
        consumer: str,
        pass_id: str,
    ) -> dict[str, Any] | None:
        """Return exact immutable completion before any external catalog I/O."""
        consumer_name = _name(consumer, field="consumer", pattern=_CONSUMER_RE)
        normalized_pass = _name(pass_id, field="pass_id", pattern=_PASS_RE)
        return self._any_full_sweep_result_from_ledger(
            self._con,
            consumer=consumer_name,
            pass_id=normalized_pass,
        )

    def record_full_sweep_completion(
        self,
        *,
        consumer: str,
        pass_id: str,
        catalog_completion_loader: Callable[[], dict[str, Any]],
    ) -> dict[str, Any]:
        """Persist exact completed catalog traversal evidence without source ack.

        Replay is probed before invoking ``catalog_completion_loader``.  A new
        effect first proves the materialized pass has no pending work, obtains
        current-admin-scoped catalog evidence outside a source transaction,
        then rechecks source/pass/work state in one source transaction.
        """
        consumer_name = _name(consumer, field="consumer", pattern=_CONSUMER_RE)
        normalized_pass = _name(pass_id, field="pass_id", pattern=_PASS_RE)
        if not callable(catalog_completion_loader):
            raise ValueError("catalog_completion_loader required")
        replay = self._any_full_sweep_result_from_ledger(
            self._con,
            consumer=consumer_name,
            pass_id=normalized_pass,
        )
        if replay is not None:
            return replay

        def require_ready(con: sqlite3.Connection) -> sqlite3.Row:
            passed = con.execute(
                "SELECT * FROM notion_legacy_change_consumer_passes "
                "WHERE consumer=? AND pass_id=? AND source_owner_id=?",
                (consumer_name, normalized_pass, self._owner),
            ).fetchone()
            pending = con.execute(
                "SELECT COUNT(*) FROM notion_legacy_change_consumer_work "
                "WHERE consumer=? AND pass_id=? AND status='pending'",
                (consumer_name, normalized_pass),
            ).fetchone()
            workspaces, non_acknowledgeable = self._terminal_workspaces(
                con, consumer=consumer_name, pass_id=normalized_pass
            )
            if (
                not passed
                or str(passed["status"]) != "materialized"
                or not bool(passed["full_sweep_required"])
                or int(pending[0] if pending else -1) != 0
                or non_acknowledgeable != 0
                or len(workspaces) > 1
            ):
                raise LegacyChangeConsumerConflict(
                    "required full sweep is not ready for completion"
                )
            self._assert_current_source(con)
            return passed

        self._con.execute("BEGIN")
        try:
            passed = require_ready(self._con)
            source_fence = {
                "source_epoch": int(passed["source_epoch"]),
                "full_sweep_generation": int(passed["full_sweep_generation"]),
                "source_device": int(passed["source_device"]),
                "source_inode": int(passed["source_inode"]),
                "source_link_count": int(passed["source_link_count"]),
            }
        finally:
            self._con.rollback()

        catalog_completion = catalog_completion_loader()
        result = validate_full_sweep_completion_result(
            {
                "pass_id": normalized_pass,
                "source_owner_id": self._owner,
                "source_fence": source_fence,
                "pending_work_count": 0,
                "catalog_completion": catalog_completion,
            }
        )
        request = {
            "pass_id": normalized_pass,
            "source_owner_id": self._owner,
            "source_fence": source_fence,
            "catalog_completion": catalog_completion,
        }
        initial_workspaces, _non_acknowledgeable = self._terminal_workspaces(
            self._con, consumer=consumer_name, pass_id=normalized_pass
        )
        if initial_workspaces and initial_workspaces != {
            str(catalog_completion["workspace_id"])
        }:
            raise LegacyChangeConsumerConflict(
                "completed catalog traversal does not cover source work"
            )

        def handler(con: sqlite3.Connection, _now: int) -> dict[str, Any]:
            current = require_ready(con)
            current_fence = {
                "source_epoch": int(current["source_epoch"]),
                "full_sweep_generation": int(current["full_sweep_generation"]),
                "source_device": int(current["source_device"]),
                "source_inode": int(current["source_inode"]),
                "source_link_count": int(current["source_link_count"]),
            }
            if current_fence != source_fence:
                raise LegacyChangeConsumerSourceFenceConflict(
                    "legacy change-consumer source generation changed"
                )
            workspaces, non_acknowledgeable = self._terminal_workspaces(
                con, consumer=consumer_name, pass_id=normalized_pass
            )
            if non_acknowledgeable != 0 or workspaces != initial_workspaces:
                raise LegacyChangeConsumerSourceFenceConflict(
                    "legacy change-consumer work coverage changed"
                )
            return result

        return self._mutate(
            consumer=consumer_name,
            operation_id=self._full_sweep_operation_id(normalized_pass),
            action=FULL_SWEEP_COMPLETION_ACTION,
            request=request,
            handler=handler,
        )

    def record_full_sweep_set_completion(
        self,
        *,
        consumer: str,
        pass_id: str,
        catalog_completions_loader: Callable[[], list[dict[str, Any]]],
    ) -> dict[str, Any]:
        """Persist a bounded exact set of completed workspace traversals."""
        consumer_name = _name(consumer, field="consumer", pattern=_CONSUMER_RE)
        normalized_pass = _name(pass_id, field="pass_id", pattern=_PASS_RE)
        if not callable(catalog_completions_loader):
            raise ValueError("catalog_completions_loader required")
        replay = self._any_full_sweep_result_from_ledger(
            self._con,
            consumer=consumer_name,
            pass_id=normalized_pass,
        )
        if replay is not None:
            return replay

        def require_ready(
            con: sqlite3.Connection,
        ) -> tuple[sqlite3.Row, set[str]]:
            passed = con.execute(
                "SELECT * FROM notion_legacy_change_consumer_passes "
                "WHERE consumer=? AND pass_id=? AND source_owner_id=?",
                (consumer_name, normalized_pass, self._owner),
            ).fetchone()
            pending = int(
                con.execute(
                    "SELECT COUNT(*) FROM notion_legacy_change_consumer_work "
                    "WHERE consumer=? AND pass_id=? AND status='pending'",
                    (consumer_name, normalized_pass),
                ).fetchone()[0]
            )
            workspaces, non_acknowledgeable = self._terminal_workspaces(
                con, consumer=consumer_name, pass_id=normalized_pass
            )
            if (
                not passed
                or str(passed["status"]) != "materialized"
                or not bool(passed["full_sweep_required"])
                or pending != 0
                or non_acknowledgeable != 0
            ):
                raise LegacyChangeConsumerConflict(
                    "required full sweep is not ready for completion"
                )
            self._assert_current_source(con)
            return passed, workspaces

        self._con.execute("BEGIN")
        try:
            passed, initial_workspaces = require_ready(self._con)
            source_fence = {
                "source_epoch": int(passed["source_epoch"]),
                "full_sweep_generation": int(passed["full_sweep_generation"]),
                "source_device": int(passed["source_device"]),
                "source_inode": int(passed["source_inode"]),
                "source_link_count": int(passed["source_link_count"]),
            }
        finally:
            self._con.rollback()

        catalog_completions = catalog_completions_loader()
        result = validate_full_sweep_set_completion_result(
            {
                "pass_id": normalized_pass,
                "source_owner_id": self._owner,
                "source_fence": source_fence,
                "pending_work_count": 0,
                "catalog_completions": catalog_completions,
            }
        )
        completion_workspaces = {
            str(item["workspace_id"]) for item in catalog_completions
        }
        if initial_workspaces and initial_workspaces != completion_workspaces:
            raise LegacyChangeConsumerConflict(
                "completed catalog traversals do not cover source work"
            )
        request = {
            "pass_id": normalized_pass,
            "source_owner_id": self._owner,
            "source_fence": source_fence,
            "catalog_completions": catalog_completions,
        }

        def handler(con: sqlite3.Connection, _now: int) -> dict[str, Any]:
            current, current_workspaces = require_ready(con)
            current_fence = {
                "source_epoch": int(current["source_epoch"]),
                "full_sweep_generation": int(current["full_sweep_generation"]),
                "source_device": int(current["source_device"]),
                "source_inode": int(current["source_inode"]),
                "source_link_count": int(current["source_link_count"]),
            }
            if current_fence != source_fence or current_workspaces != initial_workspaces:
                raise LegacyChangeConsumerSourceFenceConflict(
                    "legacy change-consumer work coverage changed"
                )
            return result

        return self._mutate(
            consumer=consumer_name,
            operation_id=self._full_sweep_set_operation_id(normalized_pass),
            action=FULL_SWEEP_SET_COMPLETION_ACTION,
            request=request,
            handler=handler,
        )


__all__ = [
    "LegacyChangeConsumerConflict",
    "LegacyChangeConsumerCounters",
    "LegacyChangeConsumerLeaseConflict",
    "LegacyChangeConsumerSourceFenceConflict",
    "LegacyChangeConsumerStore",
    "LegacyChangeConsumerWork",
    "LegacyChangeConsumerWorkBatch",
]

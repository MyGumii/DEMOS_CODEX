"""Side-effect-free v3 contract for durable legacy change-consumer work.

The owner shard remains the canonical home of its coalesced page watermarks.
This additive contract records bounded scan passes and immutable page receipts
without deleting or acknowledging a watermark.  Catalog effects and later
source acknowledgement remain separate transactions by design.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from typing import Any

from code_assist_v1 import notion_legacy_incarnation_contract as v2
from code_assist_v1.notion_legacy_change_contract import (
    MAX_GENERATION,
    MIGRATION_VERSION as V1_MIGRATION_VERSION,
)
from code_assist_v1.notion_legacy_changes import (
    legacy_incarnation_receipt_sha256,
)


MIGRATION_VERSION = 2026081303
MIGRATION_NAME = "notion_legacy_change_consumer_v3"
MIGRATION_CHECKSUM = (
    "sha256:9fc07c846c9e3e1aae98b3a8a8f0507a37fcf06a9ed203e4a5d650c4f3f71495"
)
EXPECTED_OBJECT_HASH = (
    "sha256:69a96d7b7c2affd2c0ea83ce80064853d8e12ee8d3f0fbcf27fae3a14f429e6b"
)

CONSUMER_WRITE_CONTEXT_UDF = "notion_change_consumer_write_context"
CONSUMER_NOW_UDF = "notion_change_consumer_now"
MAX_BATCH_ROWS = 32
MAX_LEASE_SECONDS = 3600
MAX_TIMESTAMP = 253_402_300_799
MAX_COUNTER = 9_007_199_254_740_991
FULL_SWEEP_COMPLETION_ACTION = "legacy.change_consumer.full_sweep_completed"
FULL_SWEEP_OPERATION_PREFIX = "full-sweep:"
FULL_SWEEP_SET_COMPLETION_ACTION = (
    "legacy.change_consumer.full_sweep_set_completed"
)
FULL_SWEEP_SET_OPERATION_PREFIX = "full-sweep-set:"

_HEALING_COUNTER_FIELDS = (
    "scanned",
    "visited",
    "match",
    "safe_miss",
    "stale",
    "superseded",
    "ambiguous",
    "permission_mismatch",
    "unsafe_path",
    "catalog_unavailable",
    "oversize",
    "source_missing",
)
_HEALING_OUTCOME_FIELDS = _HEALING_COUNTER_FIELDS[2:]

_CONSUMER_RE = re.compile(r"^[a-z][a-z0-9_.-]{1,63}$")
_WORKER_RE = _CONSUMER_RE
_PASS_RE = re.compile(r"^lpass_[0-9a-f]{32}$")
_TOKEN_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_OPERATION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}$")


class LegacyChangeConsumerContractError(Exception):
    """Sanitized failure to authenticate the v3 consumer contract."""


def validate_full_sweep_completion_result(value: Any) -> dict[str, Any]:
    """Validate the immutable, source-local proof of one catalog traversal.

    This result is deliberately private to the source operation ledger.  It
    commits identifiers needed for exact replay, while public status/report
    APIs expose only aggregate counters.
    """
    if not isinstance(value, dict) or set(value) != {
        "pass_id",
        "source_owner_id",
        "source_fence",
        "pending_work_count",
        "catalog_completion",
    }:
        raise LegacyChangeConsumerContractError(
            "legacy change consumer completion evidence is invalid"
        )
    pass_id = value.get("pass_id")
    owner = value.get("source_owner_id")
    fence = value.get("source_fence")
    catalog = value.get("catalog_completion")
    if (
        not isinstance(pass_id, str)
        or _PASS_RE.fullmatch(pass_id) is None
        or not isinstance(owner, str)
        or not 2 <= len(owner) <= 64
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", owner) is None
        or value.get("pending_work_count") != 0
        or isinstance(value.get("pending_work_count"), bool)
        or not isinstance(fence, dict)
        or set(fence) != {
            "source_epoch",
            "full_sweep_generation",
            "source_device",
            "source_inode",
            "source_link_count",
        }
        or not isinstance(catalog, dict)
        or set(catalog) != {
            "workspace_id",
            "healing_run_id",
            "source_owner_subject_id",
            "healing_mode",
            "status",
            "has_more",
            "version",
            "completed_at",
            "counters",
            "source_fence",
        }
    ):
        raise LegacyChangeConsumerContractError(
            "legacy change consumer completion evidence is invalid"
        )
    for field in ("source_epoch", "full_sweep_generation"):
        item = fence.get(field)
        if isinstance(item, bool) or not isinstance(item, int) or not 1 <= item <= MAX_GENERATION:
            raise LegacyChangeConsumerContractError(
                "legacy change consumer completion evidence is invalid"
            )
    for field in ("source_device", "source_inode"):
        item = fence.get(field)
        if (
            isinstance(item, bool)
            or not isinstance(item, int)
            or not -(2**63) <= item <= 2**63 - 1
        ):
            raise LegacyChangeConsumerContractError(
                "legacy change consumer completion evidence is invalid"
            )
    link_count = fence.get("source_link_count")
    if (
        isinstance(link_count, bool)
        or not isinstance(link_count, int)
        or not 1 <= link_count <= 65_535
    ):
        raise LegacyChangeConsumerContractError(
            "legacy change consumer completion evidence is invalid"
        )
    workspace_id = catalog.get("workspace_id")
    run_id = catalog.get("healing_run_id")
    version = catalog.get("version")
    completed_at = catalog.get("completed_at")
    counters = catalog.get("counters")
    if (
        not isinstance(workspace_id, str)
        or re.fullmatch(r"ws_[0-9a-f]{32}", workspace_id) is None
        or not isinstance(run_id, str)
        or re.fullmatch(r"wheal_[0-9a-f]{32}", run_id) is None
        or catalog.get("source_owner_subject_id") != owner
        or catalog.get("healing_mode") != "locator_apply"
        or catalog.get("status") != "completed"
        or catalog.get("has_more") is not False
        or isinstance(version, bool)
        or not isinstance(version, int)
        or not 1 <= version <= MAX_GENERATION
        or isinstance(completed_at, bool)
        or not isinstance(completed_at, int)
        or not 0 <= completed_at <= MAX_TIMESTAMP
        or catalog.get("source_fence") != fence
        or not isinstance(counters, dict)
        or set(counters) != set(_HEALING_COUNTER_FIELDS)
    ):
        raise LegacyChangeConsumerContractError(
            "legacy change consumer completion evidence is invalid"
        )
    for field in _HEALING_COUNTER_FIELDS:
        item = counters.get(field)
        if (
            isinstance(item, bool)
            or not isinstance(item, int)
            or not 0 <= item <= MAX_COUNTER
        ):
            raise LegacyChangeConsumerContractError(
                "legacy change consumer completion evidence is invalid"
            )
    if (
        sum(counters[field] for field in _HEALING_OUTCOME_FIELDS)
        != counters["visited"]
        or counters["visited"] > counters["scanned"]
    ):
        raise LegacyChangeConsumerContractError(
            "legacy change consumer completion evidence is invalid"
        )
    return value


def validate_full_sweep_set_completion_result(value: Any) -> dict[str, Any]:
    """Validate a bounded exact set of completed workspace traversals."""
    if not isinstance(value, dict) or set(value) != {
        "pass_id",
        "source_owner_id",
        "source_fence",
        "pending_work_count",
        "catalog_completions",
    }:
        raise LegacyChangeConsumerContractError(
            "legacy change consumer completion evidence is invalid"
        )
    completions = value.get("catalog_completions")
    if not isinstance(completions, list) or not 1 <= len(completions) <= MAX_BATCH_ROWS:
        raise LegacyChangeConsumerContractError(
            "legacy change consumer completion evidence is invalid"
        )
    normalized: list[dict[str, Any]] = []
    for completion in completions:
        checked = validate_full_sweep_completion_result(
            {
                "pass_id": value.get("pass_id"),
                "source_owner_id": value.get("source_owner_id"),
                "source_fence": value.get("source_fence"),
                "pending_work_count": value.get("pending_work_count"),
                "catalog_completion": completion,
            }
        )
        normalized.append(checked["catalog_completion"])
    keys = [
        (str(item["workspace_id"]), str(item["healing_run_id"]))
        for item in normalized
    ]
    if keys != sorted(keys) or len({item[0] for item in keys}) != len(keys):
        raise LegacyChangeConsumerContractError(
            "legacy change consumer completion evidence is invalid"
        )
    return value


def _context_guard() -> str:
    return (
        f"typeof({CONSUMER_WRITE_CONTEXT_UDF}())<>'integer' "
        f"OR {CONSUMER_WRITE_CONTEXT_UDF}() IS NOT 1"
    )


def _now_guard() -> str:
    return (
        f"typeof({CONSUMER_NOW_UDF}())<>'integer' "
        f"OR {CONSUMER_NOW_UDF}() NOT BETWEEN 0 AND {MAX_TIMESTAMP}"
    )


_DDL_STATEMENTS: tuple[str, ...] = (
    f"""
    CREATE TABLE notion_legacy_change_consumer_passes(
      consumer TEXT NOT NULL CHECK(
        typeof(consumer)='text' AND length(consumer) BETWEEN 2 AND 64 AND
        substr(consumer,1,1) GLOB '[a-z]' AND
        consumer NOT GLOB '*[^a-z0-9_.-]*'
      ),
      pass_id TEXT NOT NULL CHECK(
        typeof(pass_id)='text' AND length(pass_id)=38 AND
        substr(pass_id,1,6)='lpass_' AND
        substr(pass_id,7) NOT GLOB '*[^0-9a-f]*'
      ),
      source_owner_id TEXT NOT NULL CHECK(
        typeof(source_owner_id)='text' AND length(source_owner_id) BETWEEN 2 AND 64 AND
        substr(source_owner_id,1,1) GLOB '[A-Za-z0-9]' AND
        source_owner_id NOT GLOB '*[^A-Za-z0-9_.-]*'
      ),
      status TEXT NOT NULL CHECK(status IN ('running','materialized','failed')),
      source_epoch INTEGER NOT NULL CHECK(
        typeof(source_epoch)='integer' AND source_epoch BETWEEN 1 AND {MAX_GENERATION}
      ),
      full_sweep_generation INTEGER NOT NULL CHECK(
        typeof(full_sweep_generation)='integer' AND
        full_sweep_generation BETWEEN 1 AND {MAX_GENERATION}
      ),
      source_device INTEGER NOT NULL CHECK(typeof(source_device)='integer'),
      source_inode INTEGER NOT NULL CHECK(typeof(source_inode)='integer'),
      source_link_count INTEGER NOT NULL CHECK(
        typeof(source_link_count)='integer' AND source_link_count BETWEEN 1 AND 65535
      ),
      full_sweep_required INTEGER NOT NULL CHECK(
        typeof(full_sweep_required)='integer' AND full_sweep_required IN (0,1)
      ),
      cursor_page_id TEXT CHECK(
        cursor_page_id IS NULL OR (
          typeof(cursor_page_id)='text' AND length(cursor_page_id) BETWEEN 1 AND 160 AND
          cursor_page_id=trim(cursor_page_id)
        )
      ),
      has_more INTEGER NOT NULL CHECK(
        typeof(has_more)='integer' AND has_more IN (0,1)
      ),
      scanned_count INTEGER NOT NULL CHECK(
        typeof(scanned_count)='integer' AND scanned_count BETWEEN 0 AND {MAX_COUNTER}
      ),
      materialized_count INTEGER NOT NULL CHECK(
        typeof(materialized_count)='integer' AND
        materialized_count BETWEEN 0 AND {MAX_COUNTER}
      ),
      duplicate_count INTEGER NOT NULL CHECK(
        typeof(duplicate_count)='integer' AND duplicate_count BETWEEN 0 AND {MAX_COUNTER}
      ),
      lease_worker TEXT CHECK(
        lease_worker IS NULL OR (
          typeof(lease_worker)='text' AND length(lease_worker) BETWEEN 2 AND 64 AND
          substr(lease_worker,1,1) GLOB '[a-z]' AND
          lease_worker NOT GLOB '*[^a-z0-9_.-]*'
        )
      ),
      lease_token_sha256 TEXT CHECK(
        lease_token_sha256 IS NULL OR (
          typeof(lease_token_sha256)='text' AND length(lease_token_sha256)=71 AND
          substr(lease_token_sha256,1,7)='sha256:' AND
          substr(lease_token_sha256,8) NOT GLOB '*[^0-9a-f]*'
        )
      ),
      lease_expires_at INTEGER CHECK(
        lease_expires_at IS NULL OR (
          typeof(lease_expires_at)='integer' AND
          lease_expires_at BETWEEN 0 AND {MAX_TIMESTAMP}
        )
      ),
      fencing_generation INTEGER NOT NULL CHECK(
        typeof(fencing_generation)='integer' AND
        fencing_generation BETWEEN 1 AND {MAX_GENERATION}
      ),
      row_version INTEGER NOT NULL CHECK(
        typeof(row_version)='integer' AND row_version BETWEEN 1 AND {MAX_GENERATION}
      ),
      failure_code TEXT CHECK(
        failure_code IS NULL OR (
          typeof(failure_code)='text' AND length(failure_code) BETWEEN 1 AND 64 AND
          substr(failure_code,1,1) GLOB '[a-z]' AND
          failure_code NOT GLOB '*[^a-z0-9_.-]*'
        )
      ),
      started_at INTEGER NOT NULL CHECK(
        typeof(started_at)='integer' AND started_at BETWEEN 0 AND {MAX_TIMESTAMP}
      ),
      updated_at INTEGER NOT NULL CHECK(
        typeof(updated_at)='integer' AND updated_at BETWEEN started_at AND {MAX_TIMESTAMP}
      ),
      materialized_at INTEGER CHECK(
        materialized_at IS NULL OR (
          typeof(materialized_at)='integer' AND
          materialized_at BETWEEN started_at AND {MAX_TIMESTAMP}
        )
      ),
      failed_at INTEGER CHECK(
        failed_at IS NULL OR (
          typeof(failed_at)='integer' AND failed_at BETWEEN started_at AND {MAX_TIMESTAMP}
        )
      ),
      PRIMARY KEY(consumer,pass_id),
      CHECK(materialized_count+duplicate_count=scanned_count),
      CHECK(
        (status='running' AND has_more=1 AND lease_worker IS NOT NULL AND
          lease_token_sha256 IS NOT NULL AND lease_expires_at IS NOT NULL AND
          failure_code IS NULL AND materialized_at IS NULL AND failed_at IS NULL) OR
        (status='materialized' AND has_more=0 AND lease_worker IS NULL AND
          lease_token_sha256 IS NULL AND lease_expires_at IS NULL AND
          failure_code IS NULL AND materialized_at IS NOT NULL AND failed_at IS NULL) OR
        (status='failed' AND lease_worker IS NULL AND lease_token_sha256 IS NULL AND
          lease_expires_at IS NULL AND failure_code IS NOT NULL AND
          materialized_at IS NULL AND failed_at IS NOT NULL)
      )
    ) WITHOUT ROWID
    """.strip(),
    "CREATE UNIQUE INDEX idx_notion_legacy_change_consumer_one_running "
    "ON notion_legacy_change_consumer_passes(consumer) WHERE status='running'",
    f"""
    CREATE TABLE notion_legacy_change_consumer_work(
      consumer TEXT NOT NULL,
      pass_id TEXT NOT NULL,
      source_owner_id TEXT NOT NULL,
      page_id TEXT NOT NULL CHECK(
        typeof(page_id)='text' AND length(page_id) BETWEEN 1 AND 160
      ),
      source_epoch INTEGER NOT NULL CHECK(
        typeof(source_epoch)='integer' AND source_epoch BETWEEN 1 AND {MAX_GENERATION}
      ),
      full_sweep_generation INTEGER NOT NULL CHECK(
        typeof(full_sweep_generation)='integer' AND
        full_sweep_generation BETWEEN 1 AND {MAX_GENERATION}
      ),
      change_generation INTEGER NOT NULL CHECK(
        typeof(change_generation)='integer' AND
        change_generation BETWEEN 1 AND {MAX_GENERATION}
      ),
      change_mask INTEGER NOT NULL CHECK(
        typeof(change_mask)='integer' AND change_mask BETWEEN 1 AND 31
      ),
      source_exists INTEGER NOT NULL CHECK(
        typeof(source_exists)='integer' AND source_exists IN (0,1)
      ),
      source_active INTEGER NOT NULL CHECK(
        typeof(source_active)='integer' AND source_active IN (0,1)
      ),
      incarnation_id TEXT NOT NULL CHECK(
        typeof(incarnation_id)='text' AND length(incarnation_id)=36 AND
        substr(incarnation_id,1,4)='inc_' AND
        substr(incarnation_id,5) NOT GLOB '*[^0-9a-f]*'
      ),
      incarnation_generation INTEGER NOT NULL CHECK(
        typeof(incarnation_generation)='integer' AND
        incarnation_generation BETWEEN 1 AND {MAX_GENERATION}
      ),
      incarnation_retired INTEGER NOT NULL CHECK(
        typeof(incarnation_retired)='integer' AND incarnation_retired IN (0,1)
      ),
      receipt_sha256 TEXT NOT NULL CHECK(
        typeof(receipt_sha256)='text' AND length(receipt_sha256)=71 AND
        substr(receipt_sha256,1,7)='sha256:' AND
        substr(receipt_sha256,8) NOT GLOB '*[^0-9a-f]*'
      ),
      status TEXT NOT NULL CHECK(status IN ('pending','applied','ignored','superseded')),
      effect_workspace_id TEXT,
      effect_binding_generation INTEGER,
      effect_operation_id TEXT,
      effect_disposition TEXT,
      row_version INTEGER NOT NULL CHECK(
        typeof(row_version)='integer' AND row_version BETWEEN 1 AND {MAX_GENERATION}
      ),
      created_at INTEGER NOT NULL CHECK(
        typeof(created_at)='integer' AND created_at BETWEEN 0 AND {MAX_TIMESTAMP}
      ),
      applied_at INTEGER CHECK(
        applied_at IS NULL OR (
          typeof(applied_at)='integer' AND applied_at BETWEEN created_at AND {MAX_TIMESTAMP}
        )
      ),
      PRIMARY KEY(consumer,pass_id,page_id),
      UNIQUE(consumer,source_epoch,page_id,change_generation),
      FOREIGN KEY(consumer,pass_id)
        REFERENCES notion_legacy_change_consumer_passes(consumer,pass_id)
        ON DELETE RESTRICT,
      CHECK(source_active=0 OR source_exists=1),
      CHECK(incarnation_retired=0 OR source_exists=0),
      CHECK(
        (status='pending' AND effect_workspace_id IS NULL AND
          effect_binding_generation IS NULL AND effect_operation_id IS NULL AND
          effect_disposition IS NULL AND applied_at IS NULL) OR
        (status<>'pending' AND effect_operation_id IS NOT NULL AND
          effect_disposition IS NOT NULL AND applied_at IS NOT NULL)
      )
    ) WITHOUT ROWID
    """.strip(),
    "CREATE INDEX idx_notion_legacy_change_consumer_work_pending "
    "ON notion_legacy_change_consumer_work(consumer,status,source_epoch,page_id)",
    f"""
    CREATE TABLE notion_legacy_change_consumer_operations(
      consumer TEXT NOT NULL,
      operation_id TEXT NOT NULL CHECK(
        typeof(operation_id)='text' AND length(operation_id) BETWEEN 1 AND 200 AND
        operation_id=trim(operation_id)
      ),
      action TEXT NOT NULL CHECK(
        typeof(action)='text' AND length(action) BETWEEN 1 AND 80
      ),
      request_sha256 TEXT NOT NULL CHECK(
        typeof(request_sha256)='text' AND length(request_sha256)=71 AND
        substr(request_sha256,1,7)='sha256:' AND
        substr(request_sha256,8) NOT GLOB '*[^0-9a-f]*'
      ),
      result_json TEXT NOT NULL CHECK(typeof(result_json)='text'),
      created_at INTEGER NOT NULL CHECK(
        typeof(created_at)='integer' AND created_at BETWEEN 0 AND {MAX_TIMESTAMP}
      ),
      PRIMARY KEY(consumer,operation_id)
    ) WITHOUT ROWID
    """.strip(),
    f"""
    CREATE TRIGGER trg_notion_change_consumer_pass_initial
    BEFORE INSERT ON notion_legacy_change_consumer_passes
    WHEN {_context_guard()} OR {_now_guard()}
      OR NEW.status<>'running' OR NEW.has_more<>1
      OR NEW.cursor_page_id IS NOT NULL OR NEW.scanned_count<>0
      OR NEW.materialized_count<>0 OR NEW.duplicate_count<>0
      OR NEW.fencing_generation<>1 OR NEW.row_version<>1
      OR NEW.lease_worker IS NULL OR NEW.lease_token_sha256 IS NULL
      OR NEW.lease_expires_at<={CONSUMER_NOW_UDF}()
      OR NEW.lease_expires_at>{CONSUMER_NOW_UDF}()+{MAX_LEASE_SECONDS}
      OR NEW.started_at<>NEW.updated_at OR NEW.updated_at>{CONSUMER_NOW_UDF}()
      OR NEW.failure_code IS NOT NULL OR NEW.materialized_at IS NOT NULL
      OR NEW.failed_at IS NOT NULL
      OR EXISTS(
        SELECT 1 FROM notion_legacy_change_consumer_work w
        WHERE w.consumer=NEW.consumer AND w.status='pending'
      )
    BEGIN
      SELECT RAISE(ABORT,'legacy change consumer pass must start pristine');
    END
    """.strip(),
    f"""
    CREATE TRIGGER trg_notion_change_consumer_pass_transition
    BEFORE UPDATE ON notion_legacy_change_consumer_passes
    WHEN {_context_guard()} OR {_now_guard()} OR NOT (
      OLD.status='running'
      AND NEW.consumer=OLD.consumer AND NEW.pass_id=OLD.pass_id
      AND NEW.source_owner_id=OLD.source_owner_id
      AND NEW.source_epoch=OLD.source_epoch
      AND NEW.full_sweep_generation=OLD.full_sweep_generation
      AND NEW.source_device=OLD.source_device
      AND NEW.source_inode=OLD.source_inode
      AND NEW.source_link_count=OLD.source_link_count
      AND NEW.full_sweep_required=OLD.full_sweep_required
      AND NEW.started_at=OLD.started_at
      AND NEW.row_version=OLD.row_version+1
      AND NEW.updated_at>=OLD.updated_at
      AND NEW.updated_at<={CONSUMER_NOW_UDF}()
      AND (
        (
          NEW.status IN ('running','materialized')
          AND OLD.lease_expires_at>{CONSUMER_NOW_UDF}()
          AND NEW.fencing_generation=OLD.fencing_generation
          AND NEW.scanned_count>=OLD.scanned_count
          AND NEW.scanned_count<=OLD.scanned_count+{MAX_BATCH_ROWS}
          AND NEW.materialized_count>=OLD.materialized_count
          AND NEW.duplicate_count>=OLD.duplicate_count
          AND (NEW.materialized_count-OLD.materialized_count)+
              (NEW.duplicate_count-OLD.duplicate_count)=
              NEW.scanned_count-OLD.scanned_count
          AND (
            (NEW.status='running' AND NEW.has_more=1
              AND NEW.scanned_count>OLD.scanned_count
              AND NEW.cursor_page_id IS NOT NULL
              AND (OLD.cursor_page_id IS NULL OR NEW.cursor_page_id>OLD.cursor_page_id)
              AND NEW.lease_worker IS OLD.lease_worker
              AND NEW.lease_token_sha256 IS OLD.lease_token_sha256
              AND NEW.lease_expires_at IS OLD.lease_expires_at
              AND NEW.materialized_at IS NULL)
            OR
            (NEW.status='materialized' AND NEW.has_more=0
              AND (NEW.scanned_count=OLD.scanned_count OR
                (NEW.cursor_page_id IS NOT NULL AND
                 (OLD.cursor_page_id IS NULL OR NEW.cursor_page_id>OLD.cursor_page_id)))
              AND NEW.lease_worker IS NULL AND NEW.lease_token_sha256 IS NULL
              AND NEW.lease_expires_at IS NULL
              AND NEW.materialized_at=NEW.updated_at)
          )
          AND NEW.failure_code IS NULL AND NEW.failed_at IS NULL
        ) OR (
          NEW.status='running' AND NEW.has_more=OLD.has_more
          AND OLD.lease_expires_at<={CONSUMER_NOW_UDF}()
          AND NEW.fencing_generation=OLD.fencing_generation+1
          AND NEW.cursor_page_id IS OLD.cursor_page_id
          AND NEW.scanned_count=OLD.scanned_count
          AND NEW.materialized_count=OLD.materialized_count
          AND NEW.duplicate_count=OLD.duplicate_count
          AND NEW.lease_worker IS NOT NULL
          AND NEW.lease_token_sha256 IS NOT OLD.lease_token_sha256
          AND NEW.lease_expires_at>{CONSUMER_NOW_UDF}()
          AND NEW.lease_expires_at<={CONSUMER_NOW_UDF}()+{MAX_LEASE_SECONDS}
          AND NEW.failure_code IS NULL AND NEW.materialized_at IS NULL
          AND NEW.failed_at IS NULL
        ) OR (
          NEW.status='failed' AND NEW.has_more=OLD.has_more
          AND OLD.lease_expires_at>{CONSUMER_NOW_UDF}()
          AND NEW.fencing_generation=OLD.fencing_generation
          AND NEW.cursor_page_id IS OLD.cursor_page_id
          AND NEW.scanned_count=OLD.scanned_count
          AND NEW.materialized_count=OLD.materialized_count
          AND NEW.duplicate_count=OLD.duplicate_count
          AND NEW.lease_worker IS NULL AND NEW.lease_token_sha256 IS NULL
          AND NEW.lease_expires_at IS NULL AND NEW.failure_code IS NOT NULL
          AND NEW.materialized_at IS NULL AND NEW.failed_at=NEW.updated_at
        )
      )
    )
    BEGIN
      SELECT RAISE(ABORT,'invalid legacy change consumer pass transition');
    END
    """.strip(),
    """
    CREATE TRIGGER trg_notion_change_consumer_pass_delete
    BEFORE DELETE ON notion_legacy_change_consumer_passes
    BEGIN
      SELECT RAISE(ABORT,'legacy change consumer pass history is immutable');
    END
    """.strip(),
    f"""
    CREATE TRIGGER trg_notion_change_consumer_work_insert
    BEFORE INSERT ON notion_legacy_change_consumer_work
    WHEN {_context_guard()} OR {_now_guard()}
      OR NEW.status<>'pending' OR NEW.row_version<>1
      OR NEW.created_at>{CONSUMER_NOW_UDF}()
      OR NEW.effect_workspace_id IS NOT NULL
      OR NEW.effect_binding_generation IS NOT NULL
      OR NEW.effect_operation_id IS NOT NULL
      OR NEW.effect_disposition IS NOT NULL OR NEW.applied_at IS NOT NULL
      OR NOT EXISTS(
        SELECT 1 FROM notion_legacy_change_consumer_passes p
        WHERE p.consumer=NEW.consumer AND p.pass_id=NEW.pass_id
          AND p.source_owner_id=NEW.source_owner_id
          AND p.source_epoch=NEW.source_epoch
          AND p.full_sweep_generation=NEW.full_sweep_generation
          AND p.status='running' AND p.lease_expires_at>{CONSUMER_NOW_UDF}()
      )
    BEGIN
      SELECT RAISE(ABORT,'legacy change work requires a live exact pass');
    END
    """.strip(),
    f"""
    CREATE TRIGGER trg_notion_change_consumer_work_update
    BEFORE UPDATE ON notion_legacy_change_consumer_work
    WHEN {_context_guard()} OR {_now_guard()} OR NOT (
      OLD.status='pending' AND NEW.status IN ('applied','ignored','superseded')
      AND NEW.consumer=OLD.consumer AND NEW.pass_id=OLD.pass_id
      AND NEW.source_owner_id=OLD.source_owner_id AND NEW.page_id=OLD.page_id
      AND NEW.source_epoch=OLD.source_epoch
      AND NEW.full_sweep_generation=OLD.full_sweep_generation
      AND NEW.change_generation=OLD.change_generation
      AND NEW.change_mask=OLD.change_mask
      AND NEW.source_exists=OLD.source_exists AND NEW.source_active=OLD.source_active
      AND NEW.incarnation_id=OLD.incarnation_id
      AND NEW.incarnation_generation=OLD.incarnation_generation
      AND NEW.incarnation_retired=OLD.incarnation_retired
      AND NEW.receipt_sha256=OLD.receipt_sha256
      AND NEW.row_version=OLD.row_version+1 AND NEW.created_at=OLD.created_at
      AND NEW.effect_operation_id IS NOT NULL
      AND NEW.effect_disposition IS NOT NULL
      AND NEW.applied_at BETWEEN OLD.created_at AND {CONSUMER_NOW_UDF}()
    )
    BEGIN
      SELECT RAISE(ABORT,'invalid legacy change work transition');
    END
    """.strip(),
    """
    CREATE TRIGGER trg_notion_change_consumer_work_delete
    BEFORE DELETE ON notion_legacy_change_consumer_work
    BEGIN
      SELECT RAISE(ABORT,'legacy change work history is immutable');
    END
    """.strip(),
    f"""
    CREATE TRIGGER trg_notion_change_consumer_operation_insert
    BEFORE INSERT ON notion_legacy_change_consumer_operations
    WHEN {_context_guard()} OR {_now_guard()} OR NEW.created_at>{CONSUMER_NOW_UDF}()
    BEGIN
      SELECT RAISE(ABORT,'legacy change consumer operation requires service context');
    END
    """.strip(),
    """
    CREATE TRIGGER trg_notion_change_consumer_operation_update
    BEFORE UPDATE ON notion_legacy_change_consumer_operations
    BEGIN
      SELECT RAISE(ABORT,'legacy change consumer operation is immutable');
    END
    """.strip(),
    """
    CREATE TRIGGER trg_notion_change_consumer_operation_delete
    BEFORE DELETE ON notion_legacy_change_consumer_operations
    BEGIN
      SELECT RAISE(ABORT,'legacy change consumer operation is immutable');
    END
    """.strip(),
)

_EXPECTED_OBJECTS = frozenset({
    "notion_legacy_change_consumer_passes",
    "idx_notion_legacy_change_consumer_one_running",
    "notion_legacy_change_consumer_work",
    "idx_notion_legacy_change_consumer_work_pending",
    "notion_legacy_change_consumer_operations",
    "trg_notion_change_consumer_pass_initial",
    "trg_notion_change_consumer_pass_transition",
    "trg_notion_change_consumer_pass_delete",
    "trg_notion_change_consumer_work_insert",
    "trg_notion_change_consumer_work_update",
    "trg_notion_change_consumer_work_delete",
    "trg_notion_change_consumer_operation_insert",
    "trg_notion_change_consumer_operation_update",
    "trg_notion_change_consumer_operation_delete",
})


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_text(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def object_hash(con: sqlite3.Connection) -> str:
    placeholders = ",".join("?" for _ in _EXPECTED_OBJECTS)
    rows = con.execute(
        f"SELECT type,name,sql FROM sqlite_master WHERE name IN ({placeholders}) "
        "ORDER BY type,name",
        tuple(sorted(_EXPECTED_OBJECTS)),
    ).fetchall()
    return _sha256_text(_canonical_json([tuple(row) for row in rows]))


def install_schema_for_migration(con: sqlite3.Connection) -> None:
    """Install only v3 objects on an already authenticated v2 transaction."""
    for statement in _DDL_STATEMENTS:
        con.execute(statement)


def _validate_work_receipts(con: sqlite3.Connection) -> None:
    rows = con.execute(
        """
        SELECT w.*,p.source_device,p.source_inode,p.source_link_count
        FROM notion_legacy_change_consumer_work w
        JOIN notion_legacy_change_consumer_passes p
          ON p.consumer=w.consumer AND p.pass_id=w.pass_id
        """
    )
    for row in rows:
        expected = legacy_incarnation_receipt_sha256(
            source_owner_id=str(row["source_owner_id"]),
            page_id=str(row["page_id"]),
            incarnation_id=str(row["incarnation_id"]),
            incarnation_generation=int(row["incarnation_generation"]),
            incarnation_retired=bool(row["incarnation_retired"]),
            change_generation=int(row["change_generation"]),
            change_mask=int(row["change_mask"]),
            source_exists=bool(row["source_exists"]),
            source_active=bool(row["source_active"]),
            source_epoch=int(row["source_epoch"]),
            full_sweep_generation=int(row["full_sweep_generation"]),
            source_device=int(row["source_device"]),
            source_inode=int(row["source_inode"]),
            source_link_count=int(row["source_link_count"]),
        )
        if str(row["receipt_sha256"]) != expected:
            raise LegacyChangeConsumerContractError(
                "legacy change consumer receipt is invalid"
            )


def inspect_legacy_change_consumer_contract(
    con: sqlite3.Connection, *, verify_integrity: bool = True
) -> dict[str, Any]:
    """Authenticate frozen v1+v2+v3 objects and source-local invariants."""
    try:
        present = {
            str(row[0])
            for row in con.execute(
                "SELECT name FROM sqlite_master WHERE name IN ("
                + ",".join("?" for _ in _EXPECTED_OBJECTS)
                + ")",
                tuple(sorted(_EXPECTED_OBJECTS)),
            ).fetchall()
        }
        extra_triggers = frozenset(
            name for name in _EXPECTED_OBJECTS if name.startswith("trg_")
        )
        base = v2.inspect_legacy_incarnation_contract(
            con,
            verify_integrity=verify_integrity,
            additional_expected_triggers=(extra_triggers if present else frozenset()),
        )
        if not base["installed"]:
            raise LegacyChangeConsumerContractError(
                "legacy incarnation tracking is unavailable"
            )
        markers = {
            int(row[0])
            for row in con.execute(
                "SELECT version FROM notion_legacy_schema_migrations"
            ).fetchall()
        }
        expected_markers = {V1_MIGRATION_VERSION, v2.MIGRATION_VERSION}
        if present:
            expected_markers.add(MIGRATION_VERSION)
        if markers != expected_markers:
            raise LegacyChangeConsumerContractError(
                "legacy change consumer migration lineage is not exact"
            )
        if not present:
            return {
                "installed": False,
                "object_hash": "",
                "v2_object_hash": str(base["object_hash"]),
            }
        if present != _EXPECTED_OBJECTS:
            raise LegacyChangeConsumerContractError(
                "legacy change consumer schema is partial or unknown"
            )
        marker = con.execute(
            "SELECT name,checksum,actual_schema_sha256 "
            "FROM notion_legacy_schema_migrations WHERE version=?",
            (MIGRATION_VERSION,),
        ).fetchone()
        actual = object_hash(con)
        if (
            not marker
            or str(marker["name"]) != MIGRATION_NAME
            or str(marker["checksum"]) != MIGRATION_CHECKSUM
            or str(marker["actual_schema_sha256"]) != EXPECTED_OBJECT_HASH
            or actual != EXPECTED_OBJECT_HASH
        ):
            raise LegacyChangeConsumerContractError(
                "legacy change consumer schema is invalid"
            )
        invalid_pass = con.execute(
            """
            SELECT 1 FROM notion_legacy_change_consumer_passes p
            WHERE p.materialized_count+p.duplicate_count<>p.scanned_count
               OR (p.status='running' AND (p.has_more<>1 OR p.lease_worker IS NULL
                    OR p.lease_token_sha256 IS NULL OR p.lease_expires_at IS NULL))
               OR (p.status='materialized' AND (p.has_more<>0 OR p.materialized_at IS NULL
                    OR p.lease_worker IS NOT NULL OR p.lease_token_sha256 IS NOT NULL
                    OR p.lease_expires_at IS NOT NULL))
               OR (p.status='failed' AND (p.failure_code IS NULL OR p.failed_at IS NULL))
            LIMIT 1
            """
        ).fetchone()
        orphan = con.execute(
            """
            SELECT 1 FROM notion_legacy_change_consumer_work w
            LEFT JOIN notion_legacy_change_consumer_passes p
              ON p.consumer=w.consumer AND p.pass_id=w.pass_id
            WHERE p.pass_id IS NULL OR p.source_owner_id<>w.source_owner_id
               OR p.source_epoch<>w.source_epoch
               OR p.full_sweep_generation<>w.full_sweep_generation
            LIMIT 1
            """
        ).fetchone()
        invalid_work = con.execute(
            """
            SELECT 1 FROM notion_legacy_change_consumer_work w
            WHERE (w.status='pending' AND (
                     w.row_version<>1 OR w.effect_workspace_id IS NOT NULL
                     OR w.effect_binding_generation IS NOT NULL
                     OR w.effect_operation_id IS NOT NULL
                     OR w.effect_disposition IS NOT NULL OR w.applied_at IS NOT NULL
                   ))
               OR (w.status='applied' AND (
                     w.row_version<>2 OR typeof(w.effect_workspace_id)<>'text'
                     OR length(w.effect_workspace_id)<>35
                     OR substr(w.effect_workspace_id,1,3)<>'ws_'
                     OR substr(w.effect_workspace_id,4) GLOB '*[^0-9a-f]*'
                     OR typeof(w.effect_binding_generation)<>'integer'
                     OR w.effect_binding_generation<1
                     OR w.effect_disposition NOT IN (
                       'match','safe_miss','stale','ambiguous',
                       'permission_mismatch','unsafe_path','oversize','source_missing'
                     ) OR w.applied_at IS NULL
                   ))
               OR (w.status='ignored' AND (
                     w.row_version<>2 OR w.effect_workspace_id IS NOT NULL
                     OR w.effect_binding_generation IS NOT NULL
                     OR w.effect_disposition<>'no_binding' OR w.applied_at IS NULL
                   ))
               OR (w.status='superseded' AND (
                     w.row_version<>2 OR typeof(w.effect_workspace_id)<>'text'
                     OR length(w.effect_workspace_id)<>35
                     OR substr(w.effect_workspace_id,1,3)<>'ws_'
                     OR substr(w.effect_workspace_id,4) GLOB '*[^0-9a-f]*'
                     OR typeof(w.effect_binding_generation)<>'integer'
                     OR w.effect_binding_generation<1
                     OR w.effect_disposition<>'superseded' OR w.applied_at IS NULL
                   ))
            LIMIT 1
            """
        ).fetchone()
        watermark_coverage_invalid = con.execute(
            """
            WITH latest AS (
              SELECT h.page_id,h.retired_at
              FROM notion_page_incarnation_history h
              WHERE h.incarnation_generation=(
                SELECT MAX(h2.incarnation_generation)
                FROM notion_page_incarnation_history h2
                WHERE h2.page_id=h.page_id
              )
            ), expected AS (
              SELECT l.page_id,
                     CASE WHEN p.id IS NOT NULL AND p.archived=0 THEN 1 ELSE 0 END
                       AS source_exists
              FROM latest l LEFT JOIN pages p ON p.id=l.page_id
            )
            SELECT 1 FROM expected e
            LEFT JOIN notion_page_change_watermarks w ON w.page_id=e.page_id
            WHERE w.page_id IS NULL OR w.source_exists<>e.source_exists
            UNION ALL
            SELECT 1 FROM notion_page_change_watermarks w
            LEFT JOIN latest l ON l.page_id=w.page_id
            WHERE l.page_id IS NULL
            LIMIT 1
            """
        ).fetchone()
        watermark_work_regression = con.execute(
            """
            SELECT 1 FROM notion_legacy_change_consumer_work w
            LEFT JOIN notion_page_change_watermarks m ON m.page_id=w.page_id
            WHERE m.page_id IS NULL
               OR m.change_generation<w.change_generation
               OR (m.change_mask | w.change_mask)<>m.change_mask
            LIMIT 1
            """
        ).fetchone()
        completed_passes: set[tuple[str, str]] = set()
        completion_rows = con.execute(
            """
            SELECT consumer,operation_id,action,request_sha256,result_json
            FROM notion_legacy_change_consumer_operations
            WHERE action IN (?,?) OR operation_id LIKE ? OR operation_id LIKE ?
            """,
            (
                FULL_SWEEP_COMPLETION_ACTION,
                FULL_SWEEP_SET_COMPLETION_ACTION,
                FULL_SWEEP_OPERATION_PREFIX + "%",
                FULL_SWEEP_SET_OPERATION_PREFIX + "%",
            ),
        ).fetchall()
        completion_invalid = False
        for operation in completion_rows:
            try:
                result = json.loads(str(operation["result_json"]))
                action = str(operation["action"])
                if action == FULL_SWEEP_COMPLETION_ACTION:
                    validated = validate_full_sweep_completion_result(result)
                    prefix = FULL_SWEEP_OPERATION_PREFIX
                    catalog_completions = [validated["catalog_completion"]]
                    request_catalog = {
                        "catalog_completion": validated["catalog_completion"]
                    }
                elif action == FULL_SWEEP_SET_COMPLETION_ACTION:
                    validated = validate_full_sweep_set_completion_result(result)
                    prefix = FULL_SWEEP_SET_OPERATION_PREFIX
                    catalog_completions = list(validated["catalog_completions"])
                    request_catalog = {
                        "catalog_completions": validated["catalog_completions"]
                    }
                else:
                    raise LegacyChangeConsumerContractError(
                        "legacy change consumer completion evidence is invalid"
                    )
                consumer = str(operation["consumer"])
                pass_id = str(validated["pass_id"])
                if (
                    str(operation["operation_id"]) != prefix + pass_id
                    or str(operation["result_json"])
                    != json.dumps(
                        validated,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                ):
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
                non_acknowledgeable = int(
                    con.execute(
                        "SELECT COUNT(*) FROM notion_legacy_change_consumer_work "
                        "WHERE consumer=? AND pass_id=? "
                        "AND status IN ('ignored','superseded')",
                        (consumer, pass_id),
                    ).fetchone()[0]
                )
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
                completion_workspaces = {
                    str(item["workspace_id"]) for item in catalog_completions
                }
                expected_fence = {
                    "source_epoch": int(passed["source_epoch"]),
                    "full_sweep_generation": int(passed["full_sweep_generation"]),
                    "source_device": int(passed["source_device"]),
                    "source_inode": int(passed["source_inode"]),
                    "source_link_count": int(passed["source_link_count"]),
                }
                if (
                    not passed
                    or str(passed["status"]) != "materialized"
                    or not bool(passed["full_sweep_required"])
                    or str(passed["source_owner_id"])
                    != str(validated["source_owner_id"])
                    or expected_fence != validated["source_fence"]
                    or int(pending[0] if pending else -1) != 0
                    or non_acknowledgeable != 0
                    or (bool(workspaces) and workspaces != completion_workspaces)
                ):
                    raise LegacyChangeConsumerContractError(
                        "legacy change consumer completion evidence is invalid"
                    )
                expected_request = {
                    "pass_id": pass_id,
                    "source_owner_id": str(passed["source_owner_id"]),
                    "source_fence": expected_fence,
                    **request_catalog,
                }
                expected_request_sha256 = _sha256_text(
                    json.dumps(
                        {
                            "domain": (
                                "code_assist.notion.legacy_change_consumer.request.v1"
                            ),
                            "consumer": consumer,
                            "action": action,
                            "request": expected_request,
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                )
                key = (consumer, pass_id)
                if (
                    str(operation["request_sha256"]) != expected_request_sha256
                    or key in completed_passes
                ):
                    raise LegacyChangeConsumerContractError(
                        "legacy change consumer completion evidence is invalid"
                    )
                completed_passes.add(key)
            except Exception:
                completion_invalid = True
                break
        progression_invalid = False
        ordered_passes = con.execute(
            """
            SELECT consumer,pass_id,status,full_sweep_required
            FROM notion_legacy_change_consumer_passes
            ORDER BY consumer,started_at,pass_id
            """
        ).fetchall()
        for index, passed in enumerate(ordered_passes[:-1]):
            following = ordered_passes[index + 1]
            key = (str(passed["consumer"]), str(passed["pass_id"]))
            if (
                str(following["consumer"]) == key[0]
                and str(passed["status"]) == "materialized"
                and bool(passed["full_sweep_required"])
                and key not in completed_passes
            ):
                progression_invalid = True
                break
        if (
            invalid_pass is not None
            or orphan is not None
            or invalid_work is not None
            or watermark_coverage_invalid is not None
            or watermark_work_regression is not None
            or completion_invalid
            or progression_invalid
        ):
            raise LegacyChangeConsumerContractError(
                "legacy change consumer state is invalid"
            )
        if verify_integrity:
            _validate_work_receipts(con)
        return {
            "installed": True,
            "object_hash": actual,
            "v2_object_hash": str(base["object_hash"]),
        }
    except LegacyChangeConsumerContractError:
        raise
    except Exception as exc:
        raise LegacyChangeConsumerContractError(
            "legacy change consumer schema is invalid"
        ) from exc


def _assert_frozen_contract() -> None:
    if MIGRATION_CHECKSUM.startswith("__TBD") or EXPECTED_OBJECT_HASH.startswith(
        "__TBD"
    ):
        return
    if _sha256_text("\n-- statement --\n".join(_DDL_STATEMENTS)) != MIGRATION_CHECKSUM:
        raise RuntimeError("frozen legacy change-consumer DDL drifted")
    con = sqlite3.connect(":memory:")
    try:
        install_schema_for_migration(con)
        if object_hash(con) != EXPECTED_OBJECT_HASH:
            raise RuntimeError("frozen legacy change-consumer object contract drifted")
    finally:
        con.close()


_assert_frozen_contract()


__all__ = [
    "CONSUMER_NOW_UDF",
    "CONSUMER_WRITE_CONTEXT_UDF",
    "EXPECTED_OBJECT_HASH",
    "FULL_SWEEP_COMPLETION_ACTION",
    "FULL_SWEEP_OPERATION_PREFIX",
    "FULL_SWEEP_SET_COMPLETION_ACTION",
    "FULL_SWEEP_SET_OPERATION_PREFIX",
    "LegacyChangeConsumerContractError",
    "MAX_BATCH_ROWS",
    "MAX_LEASE_SECONDS",
    "MIGRATION_CHECKSUM",
    "MIGRATION_NAME",
    "MIGRATION_VERSION",
    "inspect_legacy_change_consumer_contract",
    "install_schema_for_migration",
    "object_hash",
    "validate_full_sweep_completion_result",
    "validate_full_sweep_set_completion_result",
]

"""Fail-closed Q0 load, fault, security, and observability evidence helpers.

The helpers in this module do not start a service or infer that a local run is
target evidence.  A report is target-gate eligible only when its observed
denominators meet the frozen product floor and it carries the exact sealed
``target_windows_load`` realm attestation.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


Q0_REPORT_CONTRACT = "code_assist.notion.q0.load_fault_report.v1"
TARGET_REALM_ID = "target_windows_load"
LOCAL_DEVELOPMENT_REALM_ID = "synthetic_local_q0"
FROZEN_PRODUCT_DECISION_SHA256 = (
    "sha256:19b550449db5af3510f62be6b56f65372faeacdd9f10136aa382b73da84362a8"
)
DEFAULT_PRODUCT_DECISION = (
    Path(__file__).resolve().parents[1]
    / "docs"
    / "notion_parity"
    / "core_free_web_ui"
    / "internal_collaborative_product_decision.v1.json"
)

REQUIRED_FAULT_POINT_IDS = frozenset(
    {
        "sqlite_busy",
        "slow_consumer",
        "reconnect_storm",
        "gateway_stop_restart",
        "store_transaction_rollback",
        "after_commit_before_ack",
        "disk_reserve",
    }
)
REQUIRED_SECURITY_CONTROL_IDS = frozenset(
    {
        "csrf_fail_closed",
        "origin_fail_closed",
        "ticket_one_time",
        "ticket_subject_binding",
        "permission_epoch_replay_fence",
        "non_oracle_rejection",
        "upload_size_limit",
        "upload_authorization",
    }
)
REQUIRED_OBSERVABILITY_SIGNAL_IDS = frozenset(
    {
        "gateway_health",
        "active_connections",
        "ack_latency",
        "queue_depth",
        "queue_bytes",
        "event_tail",
        "compaction_lag",
        "compaction_age",
        "process_rss",
        "backup_generation",
        "canary_abort",
        "kill_switch",
    }
)


class Q0EvidenceError(ValueError):
    """A Q0 report or its frozen authority binding is invalid."""


@dataclass(frozen=True, slots=True)
class Q0ScaleContract:
    registered_users: int
    simultaneous_connections: int
    simultaneous_editors_per_page: int
    pages_per_workspace: int
    blocks_per_page: int
    rows_per_database: int
    sustained_operations_per_second: int
    burst_operations_per_second: int
    attachment_concurrency: int
    minimum_free_bytes: int
    minimum_free_percent: int


def _canonical_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise Q0EvidenceError("Q0 evidence must be canonical JSON") from exc


def _sha256(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _reject_duplicate_keys(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise Q0EvidenceError("duplicate JSON key")
        result[key] = value
    return result


def _load_json_bytes(path: Path) -> tuple[dict[str, Any], bytes]:
    raw = path.read_bytes()
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Q0EvidenceError("invalid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise Q0EvidenceError("JSON object required")
    return value, raw


def _exact_positive(value: Any, *, field: str) -> int:
    if type(value) is not int or value < 1:
        raise Q0EvidenceError(f"positive integer required for {field}")
    return value


def _nonnegative_number(value: Any, *, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise Q0EvidenceError(f"nonnegative number required for {field}")
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise Q0EvidenceError(f"nonnegative number required for {field}")
    return result


def load_frozen_scale_contract(
    path: Path = DEFAULT_PRODUCT_DECISION,
    *,
    expected_sha256: str = FROZEN_PRODUCT_DECISION_SHA256,
) -> Q0ScaleContract:
    """Load the user-approved scale floor from its exact frozen bytes."""

    document, raw = _load_json_bytes(Path(path))
    if _sha256(raw) != expected_sha256:
        raise Q0EvidenceError("frozen product decision hash mismatch")
    try:
        scale = document["decisions"]["target_scale_and_deployment_topology"]
        pages = scale["pages_per_workspace"]
        blocks = scale["blocks_per_page"]
        rows = scale["rows_per_database"]
        reserve = scale["disk_reserve"]
    except (KeyError, TypeError) as exc:
        raise Q0EvidenceError("frozen scale decision is incomplete") from exc
    if not isinstance(scale, dict):
        raise Q0EvidenceError("frozen scale decision must be an object")
    if scale.get("values_are") != "minimum_acceptance_load_floors_not_runtime_admission_caps":
        raise Q0EvidenceError("scale denominator semantics drift")
    if scale.get("product_quota_mode") != "no_plan_or_user_feature_storage_quota":
        raise Q0EvidenceError("product quota semantics drift")
    for name, value in (("pages_per_workspace", pages), ("blocks_per_page", blocks), ("rows_per_database", rows)):
        if not isinstance(value, dict) or value.get("product_limit") is not None:
            raise Q0EvidenceError(f"{name} product limit drift")
    if not isinstance(reserve, dict) or reserve.get("rule") != (
        "fail_new_capacity_consuming_writes_when_either_floor_is_breached_without_hiding_existing_data"
    ):
        raise Q0EvidenceError("disk reserve semantics drift")
    return Q0ScaleContract(
        registered_users=_exact_positive(scale.get("registered_users"), field="registered_users"),
        simultaneous_connections=_exact_positive(scale.get("simultaneous_connections"), field="simultaneous_connections"),
        simultaneous_editors_per_page=_exact_positive(scale.get("simultaneous_editors_per_page"), field="simultaneous_editors_per_page"),
        pages_per_workspace=_exact_positive(pages.get("acceptance_fixture_floor"), field="pages_per_workspace"),
        blocks_per_page=_exact_positive(blocks.get("acceptance_fixture_floor"), field="blocks_per_page"),
        rows_per_database=_exact_positive(rows.get("acceptance_fixture_floor"), field="rows_per_database"),
        sustained_operations_per_second=_exact_positive(scale.get("sustained_operations_per_second"), field="sustained_operations_per_second"),
        burst_operations_per_second=_exact_positive(scale.get("burst_operations_per_second"), field="burst_operations_per_second"),
        attachment_concurrency=_exact_positive(scale.get("attachment_concurrency"), field="attachment_concurrency"),
        minimum_free_bytes=_exact_positive(reserve.get("minimum_free_bytes"), field="minimum_free_bytes"),
        minimum_free_percent=_exact_positive(reserve.get("minimum_free_percent"), field="minimum_free_percent"),
    )


def nearest_rank_distribution(values: Iterable[int | float]) -> dict[str, float | int]:
    ordered = sorted(_nonnegative_number(value, field="latency") for value in values)
    if not ordered:
        raise Q0EvidenceError("at least one latency sample is required")

    def percentile(percent: int) -> float:
        index = max(0, math.ceil(len(ordered) * percent / 100) - 1)
        return ordered[index]

    return {
        "count": len(ordered),
        "minimum": ordered[0],
        "p50": percentile(50),
        "p95": percentile(95),
        "p99": percentile(99),
        "maximum": ordered[-1],
        "sum": sum(ordered),
    }


def is_sqlite_busy_error(exc: BaseException) -> bool:
    """Classify SQLite lock/busy failures without exposing database text."""

    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        message = str(current).casefold()
        if "database is locked" in message or "database table is locked" in message:
            return True
        current = current.__cause__ or current.__context__
    return False


def target_realm_attestation(environ: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Return a fail-closed, secret-free realm description for the current run."""

    values = os.environ if environ is None else environ
    target_requested = values.get("CODE_ASSIST_Q0_TARGET_LOAD", "") == "1"
    run_id = str(values.get("CODE_ASSIST_Q0_RUN_ID", "") or "").strip()
    root_identity = str(values.get("CODE_ASSIST_Q0_SYNTHETIC_ROOT_SHA256", "") or "").strip().lower()
    valid_hash = len(root_identity) == 71 and root_identity.startswith("sha256:") and all(
        character in "0123456789abcdef" for character in root_identity[7:]
    )
    eligible = bool(target_requested and run_id and valid_hash)
    return {
        "realm_id": TARGET_REALM_ID if target_requested else LOCAL_DEVELOPMENT_REALM_ID,
        "execution_class": "sealed_target_runner" if target_requested else "local_development",
        "run_id": run_id or None,
        "synthetic_root_identity_sha256": root_identity if valid_hash else None,
        "runner_owned_synthetic_root": eligible,
        "contains_real_user_content": False,
        "production_root_accessed": False,
        "target_realm_attested": eligible,
    }


def _require_id_set(report: Mapping[str, Any], field: str, expected: frozenset[str]) -> None:
    value = report.get(field)
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise Q0EvidenceError(f"{field} must be a string list")
    if set(value) != expected or len(value) != len(expected):
        raise Q0EvidenceError(f"{field} denominator mismatch")


def validate_q0_report(
    report: Mapping[str, Any],
    *,
    require_target_realm: bool,
) -> dict[str, Any]:
    """Validate a report and return its target-gate eligibility summary."""

    if report.get("contract") != Q0_REPORT_CONTRACT:
        raise Q0EvidenceError("Q0 report contract mismatch")
    scale = load_frozen_scale_contract()
    if report.get("scale_contract") != asdict(scale):
        raise Q0EvidenceError("Q0 report scale binding mismatch")
    if report.get("product_decision_sha256") != FROZEN_PRODUCT_DECISION_SHA256:
        raise Q0EvidenceError("Q0 report product decision binding mismatch")

    observed = report.get("observed_denominators")
    performance = report.get("performance")
    reliability = report.get("reliability")
    realm = report.get("realm")
    if not all(isinstance(item, dict) for item in (observed, performance, reliability, realm)):
        raise Q0EvidenceError("Q0 report object section missing")
    assert isinstance(observed, dict)
    assert isinstance(performance, dict)
    assert isinstance(reliability, dict)
    assert isinstance(realm, dict)

    denominator_fields = (
        "registered_users",
        "simultaneous_connections",
        "simultaneous_editors_per_page",
        "attachment_concurrency",
    )
    denominator_pass = all(
        _exact_positive(observed.get(field), field=field) >= getattr(scale, field)
        for field in denominator_fields
    )
    sustained_rate = _nonnegative_number(
        observed.get("sustained_operations_per_second"),
        field="sustained_operations_per_second",
    )
    burst_rate = _nonnegative_number(
        observed.get("burst_operations_per_second"),
        field="burst_operations_per_second",
    )
    rate_pass = (
        sustained_rate >= scale.sustained_operations_per_second
        and burst_rate >= scale.burst_operations_per_second
    )

    latency = performance.get("ack_latency_ms")
    if not isinstance(latency, dict) or type(latency.get("count")) is not int or latency["count"] < 1:
        raise Q0EvidenceError("ACK latency distribution missing")
    ordered_latency = [
        _nonnegative_number(latency.get(field), field=f"ack_latency_ms.{field}")
        for field in ("minimum", "p50", "p95", "p99", "maximum")
    ]
    if ordered_latency != sorted(ordered_latency):
        raise Q0EvidenceError("ACK latency percentiles are not monotonic")
    for field in (
        "sqlite_busy_count",
        "queue_lag_ms",
        "projection_lag_ms",
        "memory_peak_bytes",
        "compaction_age_seconds",
    ):
        _nonnegative_number(performance.get(field), field=field)
    if performance.get("memory_peak_bytes") == 0:
        raise Q0EvidenceError("positive process RSS observation required")

    for field in (
        "acknowledged_operation_loss_count",
        "convergence_mismatch_count",
        "post_revoke_protected_delivery_count",
        "unauthorized_upload_read_count",
    ):
        if reliability.get(field) != 0:
            raise Q0EvidenceError(f"zero required for {field}")
    _require_id_set(report, "fault_point_ids", REQUIRED_FAULT_POINT_IDS)
    _require_id_set(report, "security_control_ids", REQUIRED_SECURITY_CONTROL_IDS)
    _require_id_set(report, "observability_signal_ids", REQUIRED_OBSERVABILITY_SIGNAL_IDS)

    realm_pass = (
        realm.get("realm_id") == TARGET_REALM_ID
        and realm.get("execution_class") == "sealed_target_runner"
        and realm.get("runner_owned_synthetic_root") is True
        and realm.get("contains_real_user_content") is False
        and realm.get("production_root_accessed") is False
        and realm.get("target_realm_attested") is True
    )
    eligible = bool(denominator_pass and rate_pass and realm_pass)
    if require_target_realm and not eligible:
        raise Q0EvidenceError("report is not eligible target_windows_load evidence")
    return {
        "contract": Q0_REPORT_CONTRACT,
        "status": "pass",
        "target_gate_eligible": eligible,
        "denominator_floor_passed": denominator_pass,
        "rate_floor_passed": rate_pass,
        "realm_attestation_passed": realm_pass,
    }


def write_q0_report_create_only(
    path: Path,
    report: Mapping[str, Any],
    *,
    require_target_realm: bool = True,
) -> str:
    """Validate and create one immutable report; existing bytes are preserved."""

    validate_q0_report(report, require_target_realm=require_target_realm)
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    raw = _canonical_bytes(dict(report)) + b"\n"
    try:
        with destination.open("xb") as target:
            target.write(raw)
            target.flush()
            os.fsync(target.fileno())
    except FileExistsError as exc:
        raise Q0EvidenceError("Q0 report destination already exists") from exc
    return _sha256(raw)


__all__ = [
    "DEFAULT_PRODUCT_DECISION",
    "FROZEN_PRODUCT_DECISION_SHA256",
    "LOCAL_DEVELOPMENT_REALM_ID",
    "Q0EvidenceError",
    "Q0ScaleContract",
    "Q0_REPORT_CONTRACT",
    "REQUIRED_FAULT_POINT_IDS",
    "REQUIRED_OBSERVABILITY_SIGNAL_IDS",
    "REQUIRED_SECURITY_CONTROL_IDS",
    "TARGET_REALM_ID",
    "is_sqlite_busy_error",
    "load_frozen_scale_contract",
    "nearest_rank_distribution",
    "target_realm_attestation",
    "validate_q0_report",
    "write_q0_report_create_only",
]

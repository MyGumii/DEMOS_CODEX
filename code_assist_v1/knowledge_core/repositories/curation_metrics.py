"""Read-only P6.7 shadow/proposal-only precision evidence.

The canonical finding ledger remains authoritative.  This repository performs
no detector, model, review, or domain mutation; it derives one bounded,
body-free report from versioned finding and human-review proof rows.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import re
from typing import Literal

from ..db import ManagedConnection


_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.ASCII,
)
_MAX_SAMPLE_SIZE = 100
_ACCEPTED = ("approved", "applied", "apply_failed")


class CurationMetricsIntegrityError(RuntimeError):
    """Stored curation evidence cannot produce a trustworthy report."""


@dataclass(frozen=True, slots=True)
class CurationPrecisionSample:
    finding_id: str
    finding_kind: str
    status: str
    verdict: Literal["accepted", "rejected", "ignored"]
    model_id: str
    prompt_version: str
    reviewed_by: str
    reviewed_at_ms: int
    evidence_sha256: str

    def as_dict(self) -> dict[str, object]:
        return {
            "evidence_sha256": self.evidence_sha256,
            "finding_id": self.finding_id,
            "finding_kind": self.finding_kind,
            "model_id": self.model_id,
            "prompt_version": self.prompt_version,
            "reviewed_at_ms": self.reviewed_at_ms,
            "reviewed_by": self.reviewed_by,
            "status": self.status,
            "verdict": self.verdict,
        }


@dataclass(frozen=True, slots=True)
class CurationShadowMetrics:
    space_id: str
    finding_count: int
    pending_count: int
    human_reviewed_count: int
    model_proposal_count: int
    model_adjudicated_count: int
    model_accepted_count: int
    model_rejected_count: int
    model_ignored_count: int
    model_obsolete_count: int
    precision_numerator: int
    precision_denominator: int
    precision_ppm: int | None
    model_automatic_execution_count: int
    risky_model_automatic_execution_count: int
    t3_model_automatic_execution_count: int
    sample: tuple[CurationPrecisionSample, ...]
    report_status: Literal["pass", "fail"]
    mode: Literal["shadow_proposal_only"] = "shadow_proposal_only"
    schema_version: int = 1

    def as_dict(self) -> dict[str, object]:
        return {
            "finding_count": self.finding_count,
            "human_reviewed_count": self.human_reviewed_count,
            "mode": self.mode,
            "model_accepted_count": self.model_accepted_count,
            "model_adjudicated_count": self.model_adjudicated_count,
            "model_automatic_execution_count": self.model_automatic_execution_count,
            "model_ignored_count": self.model_ignored_count,
            "model_obsolete_count": self.model_obsolete_count,
            "model_proposal_count": self.model_proposal_count,
            "model_rejected_count": self.model_rejected_count,
            "pending_count": self.pending_count,
            "precision_denominator": self.precision_denominator,
            "precision_numerator": self.precision_numerator,
            "precision_ppm": self.precision_ppm,
            "report_status": self.report_status,
            "risky_model_automatic_execution_count": (
                self.risky_model_automatic_execution_count
            ),
            "sample": [item.as_dict() for item in self.sample],
            "schema_version": self.schema_version,
            "space_id": self.space_id,
            "t3_model_automatic_execution_count": (
                self.t3_model_automatic_execution_count
            ),
        }


def _canonical_uuid(value: object, field: str) -> str:
    if not isinstance(value, str) or _UUID_RE.fullmatch(value) is None:
        raise ValueError(f"{field} must be a canonical lowercase UUID.")
    return value


def _count(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise CurationMetricsIntegrityError(f"Stored {field} is invalid.")
    return value


def _verdict(status: str) -> Literal["accepted", "rejected", "ignored"]:
    if status in _ACCEPTED:
        return "accepted"
    if status == "rejected":
        return "rejected"
    if status == "ignored":
        return "ignored"
    raise CurationMetricsIntegrityError("A precision sample has no adjudicated verdict.")


class CurationMetricsRepository:
    """Derive deterministic, body-free shadow metrics inside a read transaction."""

    __slots__ = ()

    def read_shadow_metrics(
        self,
        connection: ManagedConnection,
        *,
        space_id: str,
        sample_size: int = 25,
    ) -> CurationShadowMetrics:
        if not isinstance(connection, ManagedConnection) or not connection.in_transaction:
            raise TypeError("Curation metrics require an active ManagedConnection transaction.")
        space = _canonical_uuid(space_id, "space_id")
        if (
            isinstance(sample_size, bool)
            or not isinstance(sample_size, int)
            or not 1 <= sample_size <= _MAX_SAMPLE_SIZE
        ):
            raise ValueError("sample_size must be in the range 1..100.")
        row = connection.execute(
            """SELECT
                 count(*) AS finding_count,
                 sum(CASE WHEN status='pending' THEN 1 ELSE 0 END) AS pending_count,
                 sum(CASE WHEN reviewed_by IS NOT NULL THEN 1 ELSE 0 END)
                   AS human_reviewed_count,
                 sum(CASE WHEN model_id IS NOT NULL THEN 1 ELSE 0 END)
                   AS model_proposal_count,
                 sum(CASE WHEN model_id IS NOT NULL AND reviewed_by IS NOT NULL
                               AND status IN ('approved','rejected','ignored','applied','apply_failed')
                          THEN 1 ELSE 0 END) AS model_adjudicated_count,
                 sum(CASE WHEN model_id IS NOT NULL AND reviewed_by IS NOT NULL
                               AND status IN ('approved','applied','apply_failed')
                          THEN 1 ELSE 0 END) AS model_accepted_count,
                 sum(CASE WHEN model_id IS NOT NULL AND reviewed_by IS NOT NULL
                               AND status='rejected' THEN 1 ELSE 0 END)
                   AS model_rejected_count,
                 sum(CASE WHEN model_id IS NOT NULL AND reviewed_by IS NOT NULL
                               AND status='ignored' THEN 1 ELSE 0 END)
                   AS model_ignored_count,
                 sum(CASE WHEN model_id IS NOT NULL AND status='obsolete'
                          THEN 1 ELSE 0 END) AS model_obsolete_count,
                 sum(CASE WHEN model_id IS NOT NULL
                               AND status IN ('applied','apply_failed')
                               AND (reviewed_by IS NULL OR applied_by IS NULL)
                          THEN 1 ELSE 0 END) AS model_automatic_execution_count,
                 sum(CASE WHEN model_id IS NOT NULL
                               AND finding_kind IN (
                                 'near_duplicate','update','potential_conflict',
                                 'semantic_edge','replace','merge','deactivate','purge')
                               AND status IN ('applied','apply_failed')
                               AND (reviewed_by IS NULL OR applied_by IS NULL)
                          THEN 1 ELSE 0 END)
                   AS risky_model_automatic_execution_count,
                 sum(CASE WHEN model_id IS NOT NULL AND risk_tier='T3'
                               AND status IN ('applied','apply_failed')
                               AND (reviewed_by IS NULL OR applied_by IS NULL)
                          THEN 1 ELSE 0 END)
                   AS t3_model_automatic_execution_count
               FROM curation_finding WHERE space_id=?""",
            (space,),
        ).fetchone()
        if row is None:
            raise CurationMetricsIntegrityError("The curation aggregate is missing.")
        values = {key: _count(row[key] or 0, key) for key in row.keys()}
        accepted = values["model_accepted_count"]
        rejected = values["model_rejected_count"]
        ignored = values["model_ignored_count"]
        denominator = accepted + rejected + ignored
        if values["model_adjudicated_count"] != denominator:
            raise CurationMetricsIntegrityError(
                "The model adjudication aggregate is internally inconsistent."
            )
        sample_rows = connection.execute(
            """SELECT id,finding_kind,status,model_id,prompt_version,reviewed_by,
                      reviewed_at,evidence_json
                 FROM curation_finding
                WHERE space_id=? AND model_id IS NOT NULL
                  AND reviewed_by IS NOT NULL
                  AND status IN ('approved','rejected','ignored','applied','apply_failed')
                ORDER BY id LIMIT ?""",
            (space, sample_size),
        ).fetchall()
        samples: list[CurationPrecisionSample] = []
        for item in sample_rows:
            finding_id = _canonical_uuid(item["id"], "finding_id")
            model_id = item["model_id"]
            prompt_version = item["prompt_version"]
            reviewed_by = item["reviewed_by"]
            reviewed_at = item["reviewed_at"]
            evidence_json = item["evidence_json"]
            if (
                not isinstance(model_id, str)
                or not model_id
                or not isinstance(prompt_version, str)
                or not prompt_version
                or not isinstance(reviewed_by, str)
                or not reviewed_by
                or isinstance(reviewed_at, bool)
                or not isinstance(reviewed_at, int)
                or reviewed_at < 0
                or not isinstance(evidence_json, str)
            ):
                raise CurationMetricsIntegrityError(
                    "Stored model-review sampling evidence is invalid."
                )
            status = str(item["status"])
            samples.append(
                CurationPrecisionSample(
                    finding_id=finding_id,
                    finding_kind=str(item["finding_kind"]),
                    status=status,
                    verdict=_verdict(status),
                    model_id=model_id,
                    prompt_version=prompt_version,
                    reviewed_by=reviewed_by,
                    reviewed_at_ms=reviewed_at,
                    evidence_sha256=sha256(evidence_json.encode("utf-8")).hexdigest(),
                )
            )
        auto_count = values["model_automatic_execution_count"]
        risky_auto_count = values["risky_model_automatic_execution_count"]
        t3_auto_count = values["t3_model_automatic_execution_count"]
        return CurationShadowMetrics(
            space_id=space,
            finding_count=values["finding_count"],
            pending_count=values["pending_count"],
            human_reviewed_count=values["human_reviewed_count"],
            model_proposal_count=values["model_proposal_count"],
            model_adjudicated_count=values["model_adjudicated_count"],
            model_accepted_count=accepted,
            model_rejected_count=rejected,
            model_ignored_count=ignored,
            model_obsolete_count=values["model_obsolete_count"],
            precision_numerator=accepted,
            precision_denominator=denominator,
            precision_ppm=(
                None if denominator == 0 else (accepted * 1_000_000) // denominator
            ),
            model_automatic_execution_count=auto_count,
            risky_model_automatic_execution_count=risky_auto_count,
            t3_model_automatic_execution_count=t3_auto_count,
            sample=tuple(samples),
            report_status=(
                "pass"
                if auto_count == 0 and risky_auto_count == 0 and t3_auto_count == 0
                else "fail"
            ),
        )


__all__ = [
    "CurationMetricsIntegrityError",
    "CurationMetricsRepository",
    "CurationPrecisionSample",
    "CurationShadowMetrics",
]

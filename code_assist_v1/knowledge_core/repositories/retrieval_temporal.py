"""Bounded v12 temporal evidence reads for already-authorized retrieval IDs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json

from ..db import ManagedConnection
from .retrieval import (
    RankedDocument,
    RankedTemporalValue,
    RetrievalIntegrityError,
    _connection,
)


MAX_TEMPORAL_DOCUMENTS = 40


@dataclass(frozen=True, slots=True)
class RetrievalTemporalEvidence:
    item_id: str
    revision_id: str
    source_space_id: str
    registered_at_ms: int
    content_modified_at_ms: int
    temporal_summary_sha256: str
    source_modified: RankedTemporalValue | None
    information_effective: RankedTemporalValue | None
    verification_current: bool
    last_verified_at_ms: int | None


def _utc_iso(milliseconds: int) -> str:
    seconds, millis = divmod(milliseconds, 1000)
    value = datetime.fromtimestamp(seconds, tz=timezone.utc).replace(
        microsecond=millis * 1000
    )
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _summary_head(row, prefix: str) -> dict[str, object] | None:
    event_id = row[f"{prefix}_event_id"]
    if event_id is None:
        return None
    state = str(row[f"{prefix}_state"])
    event_kind = str(row[f"{prefix}_event_kind"])
    if state == "revoked" and event_kind == "revoke":
        return {"event_id": str(event_id), "state": "revoked", "value": None}
    if state != "asserted" or event_kind != "assert":
        raise RetrievalIntegrityError("A current temporal head is incoherent.")
    precision = str(row[f"{prefix}_precision"])
    if precision == "instant":
        instant = row[f"{prefix}_instant_ms"]
        if isinstance(instant, bool) or not isinstance(instant, int) or instant < 0:
            raise RetrievalIntegrityError("A temporal instant is invalid.")
        value: dict[str, object] = {
            "precision": "instant",
            "value": _utc_iso(instant),
        }
    elif precision == "date":
        date_value = row[f"{prefix}_date"]
        if not isinstance(date_value, str):
            raise RetrievalIntegrityError("A temporal date is invalid.")
        value = {
            "precision": "date",
            "timezone": "Asia/Seoul",
            "value": date_value,
        }
    else:
        raise RetrievalIntegrityError("A temporal precision is invalid.")
    return {"event_id": str(event_id), "state": "asserted", "value": value}


def _active_value(row, prefix: str) -> RankedTemporalValue | None:
    if row[f"{prefix}_event_id"] is None or row[f"{prefix}_state"] != "asserted":
        return None
    precision = str(row[f"{prefix}_precision"])
    instant = row[f"{prefix}_instant_ms"]
    date_value = row[f"{prefix}_date"]
    timezone_name = row[f"{prefix}_timezone"]
    utc_offset = row[f"{prefix}_utc_offset"]
    if precision == "instant":
        if (
            isinstance(instant, bool)
            or not isinstance(instant, int)
            or instant < 0
            or not isinstance(timezone_name, str)
            or isinstance(utc_offset, bool)
            or not isinstance(utc_offset, int)
        ):
            raise RetrievalIntegrityError("A temporal instant value is invalid.")
        return RankedTemporalValue(
            precision="instant",
            instant_value_ms=instant,
            date_value=None,
            source_timezone=timezone_name,
            source_utc_offset_minutes=utc_offset,
        )
    if precision == "date" and isinstance(date_value, str):
        return RankedTemporalValue(
            precision="date",
            instant_value_ms=None,
            date_value=date_value,
            source_timezone="Asia/Seoul",
            source_utc_offset_minutes=None,
        )
    raise RetrievalIntegrityError("A temporal tagged value is invalid.")


def _summary_sha(row) -> str:
    document = {
        "information_effective_head": _summary_head(row, "effective"),
        "revision_id": str(row["revision_id"]),
        "schema_version": 1,
        "source_modified_head": _summary_head(row, "source"),
    }
    encoded = json.dumps(
        document,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    digest = sha256(encoded).hexdigest()
    source_sequence = row["source_sequence"]
    effective_sequence = row["effective_sequence"]
    stored = None
    if source_sequence is not None and (
        effective_sequence is None or int(source_sequence) > int(effective_sequence)
    ):
        stored = row["source_summary_sha256"]
    elif effective_sequence is not None:
        stored = row["effective_summary_sha256"]
    if stored is not None and stored != digest:
        raise RetrievalIntegrityError("The current temporal summary is invalid.")
    return digest


class RetrievalTemporalRepository:
    """Read temporal heads for at most forty exact authorized document IDs."""

    @staticmethod
    def load(
        connection: ManagedConnection,
        documents: tuple[RankedDocument, ...],
    ) -> dict[str, RetrievalTemporalEvidence]:
        connection = _connection(connection)
        if len(documents) > MAX_TEMPORAL_DOCUMENTS:
            raise RetrievalIntegrityError("The temporal retrieval input exceeded its cap.")
        if not documents:
            return {}
        request = [
            {
                "item_id": row.item_id,
                "ordinal": index,
                "revision_id": row.revision_id,
                "source_space_id": row.source_space_id,
            }
            for index, row in enumerate(documents)
        ]
        rows = connection.execute(
            """WITH requested(item_id,revision_id,source_space_id,ordinal) AS (
                 SELECT json_extract(value,'$.item_id'),
                        json_extract(value,'$.revision_id'),
                        json_extract(value,'$.source_space_id'),
                        json_extract(value,'$.ordinal')
                   FROM json_each(?)
               )
               SELECT requested.item_id,requested.revision_id,
                      requested.source_space_id,item.created_at AS registered_at_ms,
                      revision.created_at AS content_modified_at_ms,
                      source_head.state AS source_state,
                      source_head.temporal_summary_sha256 AS source_summary_sha256,
                      source_event.event_sequence AS source_sequence,
                      source_event.id AS source_event_id,
                      source_event.event_kind AS source_event_kind,
                      source_event.value_precision AS source_precision,
                      source_event.instant_value_ms AS source_instant_ms,
                      source_event.date_value AS source_date,
                      source_event.source_timezone AS source_timezone,
                      source_event.source_utc_offset_minutes AS source_utc_offset,
                      effective_head.state AS effective_state,
                      effective_head.temporal_summary_sha256
                        AS effective_summary_sha256,
                      effective_event.event_sequence AS effective_sequence,
                      effective_event.id AS effective_event_id,
                      effective_event.event_kind AS effective_event_kind,
                      effective_event.value_precision AS effective_precision,
                      effective_event.instant_value_ms AS effective_instant_ms,
                      effective_event.date_value AS effective_date,
                      effective_event.source_timezone AS effective_timezone,
                      effective_event.source_utc_offset_minutes
                        AS effective_utc_offset,
                      verification_head.state AS verification_state,
                      verification_head.temporal_summary_sha256
                        AS verification_summary_sha256,
                      verification_event.event_kind AS verification_event_kind,
                      verification_event.attested_at AS verification_attested_at
                 FROM requested
                 JOIN knowledge_item AS item
                   ON item.id=requested.item_id
                  AND item.space_id=requested.source_space_id
                  AND item.current_revision_id=requested.revision_id
                  AND item.lifecycle_status='active'
                  AND item.curation_status='published'
                 JOIN knowledge_revision AS revision
                   ON revision.id=requested.revision_id
                  AND revision.item_id=requested.item_id
                 LEFT JOIN revision_temporal_head AS source_head
                   ON source_head.revision_id=requested.revision_id
                  AND source_head.assertion_kind='source_modified'
                 LEFT JOIN revision_temporal_assertion AS source_event
                   ON source_event.id=source_head.head_event_id
                 LEFT JOIN revision_temporal_head AS effective_head
                   ON effective_head.revision_id=requested.revision_id
                  AND effective_head.assertion_kind='information_effective'
                 LEFT JOIN revision_temporal_assertion AS effective_event
                   ON effective_event.id=effective_head.head_event_id
                 LEFT JOIN revision_verification_head AS verification_head
                   ON verification_head.revision_id=requested.revision_id
                 LEFT JOIN revision_verification_attestation AS verification_event
                   ON verification_event.id=verification_head.head_event_id
                ORDER BY requested.ordinal""",
            (json.dumps(request, sort_keys=True, separators=(",", ":")),),
        ).fetchall()
        result: dict[str, RetrievalTemporalEvidence] = {}
        for row in rows:
            summary = _summary_sha(row)
            verification_current = bool(
                row["verification_state"] == "attested"
                and row["verification_event_kind"] == "attest"
                and row["verification_summary_sha256"] == summary
            )
            result[str(row["item_id"])] = RetrievalTemporalEvidence(
                item_id=str(row["item_id"]),
                revision_id=str(row["revision_id"]),
                source_space_id=str(row["source_space_id"]),
                registered_at_ms=int(row["registered_at_ms"]),
                content_modified_at_ms=int(row["content_modified_at_ms"]),
                temporal_summary_sha256=summary,
                source_modified=_active_value(row, "source"),
                information_effective=_active_value(row, "effective"),
                verification_current=verification_current,
                last_verified_at_ms=(
                    int(row["verification_attested_at"])
                    if verification_current
                    else None
                ),
            )
        return result


__all__ = [
    "MAX_TEMPORAL_DOCUMENTS",
    "RetrievalTemporalEvidence",
    "RetrievalTemporalRepository",
]

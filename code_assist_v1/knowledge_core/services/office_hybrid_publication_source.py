"""Restore one completed v56 Office Wiki draft for a future atomic publisher.

This is a read-only candidate verifier, not a public publication route. It
replays every retained model receipt outside the writer transaction and keeps
the original source lease until a caller completes or abandons publication.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json

from ..db import TransactionMode, db_now_ms, transaction
from ..migrations.office_hybrid_wiki_catalog import OFFICE_HYBRID_WIKI_MIGRATION_CATALOG
from ..migrations.v056_office_hybrid_wiki import VERSION
from ..native_wiki_batches import NativeBatchReceipt
from ..office_hybrid_visual_authoring import (
    OfficeVisualObservationReceipt, assemble_office_hybrid_batches,
)
from ..wiki_authoring import _hash
from .office_hybrid_authoring_source import (
    CanonicalOfficeHybridAuthoringSourceService,
    PreparedOfficeHybridAuthoringSource,
)
from .office_hybrid_wiki_start import _native_plan_sha256


class OfficeHybridPublicationSourceConflict(ValueError):
    """The completed Office draft or its current source authority changed."""


def _require(condition, message="Office hybrid publication candidate is unavailable."):
    if not condition:
        raise OfficeHybridPublicationSourceConflict(message)


@dataclass(frozen=True, slots=True)
class PreparedOfficeHybridPublicationSource:
    source: PreparedOfficeHybridAuthoringSource = field(repr=False)
    job_id: str
    content_md: str = field(repr=False)
    content_sha256: str
    manifest_json: str = field(repr=False)
    manifest_sha256: str

    def close(self):
        self.source.close()

    def __enter__(self):
        self.source.__enter__()
        return self

    def __exit__(self, *_):
        self.close()


class OfficeHybridPublicationSourceService:
    def __init__(self, factory, *, source: CanonicalOfficeHybridAuthoringSourceService):
        expected = OFFICE_HYBRID_WIKI_MIGRATION_CATALOG.migrations
        if factory.migration_catalog.migrations[:len(expected)] != expected:
            raise ValueError("Office hybrid publication requires the verified v56 catalog.")
        if (type(source) is not CanonicalOfficeHybridAuthoringSourceService
                or source.factory.database_path != factory.database_path):
            raise TypeError("Office hybrid publication requires its same-database source.")
        self.factory, self.source = factory, source

    @staticmethod
    def _receipt(row):
        model = tuple(tuple(pair) for pair in json.loads(row["model_json"]))
        if row["task_kind"] == "native_batch":
            return NativeBatchReceipt(row["task_id"], row["request_sha256"],
                row["response_text"], row["response_sha256"], model,
                row["transport_json"])
        _require(row["task_kind"] == "visual_page")
        return OfficeVisualObservationReceipt(row["ordinal"], row["task_id"],
            row["request_sha256"], row["response_text"],
            row["response_sha256"], model, row["transport_json"])

    def _registered(self, connection, source):
        row = connection.execute("""SELECT r.*,j.state,j.cancel_requested,j.result_json,
                s.job_id AS snapshot_job_id,s.actor_user_id AS snapshot_actor_user_id,
                s.content_md,s.content_sha256,s.manifest_json,s.manifest_sha256
            FROM office_hybrid_wiki_run r JOIN durable_job j ON j.id=r.job_id
            LEFT JOIN office_hybrid_wiki_snapshot s ON s.preview_id=r.preview_id
            WHERE r.preview_id=?""", (source.preview_id,)).fetchone()
        _require(row is not None)
        row = dict(row)
        expected_result = {"schema_version": 1, "preview_id": source.preview_id,
            "content_sha256": row["content_sha256"],
            "manifest_sha256": row["manifest_sha256"]}
        _require(row["state"] == "succeeded" and row["cancel_requested"] == 0
            and row["actor_user_id"] == source.actor_user_id
            and row["parent_job_id"] == source.parent_job_id
            and row["artifact_id"] == source.artifact_id
            and row["extraction_snapshot_id"] == source.native.source_plan.source.snapshot_id
            and row["workflow_version"] == VERSION
            and row["policy_sha256"] == _hash(self.source.policy.encoded())
            and row["source_fence_sha256"] == source.fence_sha256
            and row["native_plan_sha256"] == _native_plan_sha256(source.native)
            and row["visual_plan_sha256"] == source.visual.plan_sha256
            and row["native_batch_count"] == len(source.native.batches)
            and row["visual_page_count"] == len(source.visual.source.pages)
            and row["snapshot_job_id"] == row["job_id"]
            and row["snapshot_actor_user_id"] == source.actor_user_id
            and type(row["content_md"]) is str
            and type(row["manifest_json"]) is str
            and row["content_sha256"] == _hash(row["content_md"])
            and row["manifest_sha256"] == _hash(row["manifest_json"])
            and json.loads(row["result_json"]) == expected_result)
        return row

    def prepare(self, *, preview_id, actor_user_id):
        connection = self.factory.open(read_only=True)
        try:
            with transaction(connection, TransactionMode.DEFERRED):
                now_ms = db_now_ms(connection)
        finally:
            connection.close()
        source = self.source.prepare(preview_id=preview_id,
            actor_user_id=actor_user_id, now_ms=now_ms)
        try:
            connection = self.factory.open(read_only=True)
            try:
                with transaction(connection, TransactionMode.DEFERRED):
                    self.source.revalidate_in_transaction(connection, source,
                        now_ms=db_now_ms(connection))
                    row = self._registered(connection, source)
                    receipts = tuple(connection.execute("""SELECT * FROM office_hybrid_wiki_receipt
                        WHERE preview_id=? ORDER BY task_kind,ordinal""",
                        (preview_id,)))
            finally:
                connection.close()
            native = {}
            visual = {}
            for saved in receipts:
                receipt = self._receipt(saved)
                if saved["task_kind"] == "native_batch":
                    _require(saved["ordinal"] not in native)
                    native[saved["ordinal"]] = receipt
                else:
                    _require(saved["ordinal"] not in visual)
                    visual[saved["ordinal"]] = receipt
            _require(set(native) == set(range(1, len(source.native.batches) + 1))
                and set(visual) == set(range(1, len(source.visual.source.pages) + 1)))
            assembled = assemble_office_hybrid_batches(source.native,
                tuple(native[index] for index in sorted(native)), source.visual,
                tuple(visual[index] for index in sorted(visual)))
            _require((assembled.markdown, assembled.manifest_json) ==
                (row["content_md"], row["manifest_json"]),
                "Office hybrid draft differs from its retained model receipts.")
            candidate = PreparedOfficeHybridPublicationSource(source, row["job_id"],
                row["content_md"], row["content_sha256"],
                row["manifest_json"], row["manifest_sha256"])
            connection = self.factory.open(read_only=True)
            try:
                with transaction(connection, TransactionMode.DEFERRED):
                    self.revalidate_in_transaction(connection, candidate)
            finally:
                connection.close()
            return candidate
        except BaseException:
            source.close()
            raise

    def revalidate_in_transaction(self, connection, candidate):
        _require(type(candidate) is PreparedOfficeHybridPublicationSource)
        self.source.revalidate_in_transaction(connection, candidate.source,
            now_ms=db_now_ms(connection))
        row = self._registered(connection, candidate.source)
        _require((candidate.job_id, candidate.content_md,
            candidate.content_sha256, candidate.manifest_json,
            candidate.manifest_sha256) ==
            (row["job_id"], row["content_md"], row["content_sha256"],
                row["manifest_json"], row["manifest_sha256"]),
            "Office hybrid publication candidate changed before commit.")
        return candidate


__all__ = ["OfficeHybridPublicationSourceService",
    "OfficeHybridPublicationSourceConflict", "PreparedOfficeHybridPublicationSource"]

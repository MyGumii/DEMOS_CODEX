"""Explicit v2 upload-preview composition. No route or startup activation.

Old handlers/requests stay v1. Native ready extractions commit a source-bound
structure with their body, preview and events. Deterministic extraction failure
commits a failed preview without inventing an empty native structure.
"""

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path

from ..db import TransactionMode, db_now_ms, transaction
from ..migrations.native_preview_catalog import NATIVE_PREVIEW_MIGRATION_CATALOG
from ..repositories.artifacts import ArtifactBundleConflictError, ArtifactBundleIntegrityError
from ..repositories.jobs import ClaimFence
from ..repositories.native_table_artifacts import (
    NATIVE_EXTRACTOR_VERSION, NativeTableArtifactRepository,
    PreparedNativeTableExtraction, prepare_native_table_extraction,
)
from ..worker import (
    DurableWorkKernel, HandlerRegistry, JobExpectedStateConflictError,
    PermanentJobExecutionError,
)
from .native_rich_publication import CanonicalNativePublicationExtractor
from .publication import (
    CanonicalPublicationSourceLoader, PublicationPreviewExtractionHandler,
    PublicationPreviewService, _PreparedPreviewExtraction,
    publication_artifact_ids, publication_preview_id,
)


NATIVE_PREVIEW_RULE_VERSION = "deterministic-ingest-native-tables-v1"


def _require_catalog(factory):
    expected = NATIVE_PREVIEW_MIGRATION_CATALOG.migrations
    if factory.migration_catalog.migrations[:len(expected)] != expected:
        raise ValueError("Native preview requires the verified successor catalog")


class NativePublicationPreviewService(PublicationPreviewService):
    """Trusted opt-in producer. Deployment must retain both worker versions."""

    _upload_handler_version = "v2"
    _upload_rule_version = NATIVE_PREVIEW_RULE_VERSION

    def __init__(self, factory, scope_resolver, stager, **kwargs):
        _require_catalog(factory)
        if kwargs.get("wiki_upload") is not None:
            raise ValueError("Native authoring registration requires its successor contract")
        super().__init__(factory, scope_resolver, stager, **kwargs)

    def _upload_request_sha256(self, **fields):
        original = super()._upload_request_sha256(**fields)
        payload = {"legacy_request_sha256": original, "handler_version": "v2",
            "rule_version": self._upload_rule_version, "schema_version": 2}
        return sha256(json.dumps(payload, sort_keys=True,
            separators=(",", ":")).encode("utf-8")).hexdigest()

    def _upload_expected_state(self, state):
        return {**state, "handler_version": "v2", "schema_version": 2}


@dataclass(frozen=True, slots=True)
class _PreparedNativePreviewExtraction(_PreparedPreviewExtraction):
    native: PreparedNativeTableExtraction | None = None


class NativePublicationPreviewExtractionHandler(PublicationPreviewExtractionHandler):
    _handler_version = "v2"
    _rule_version = NATIVE_PREVIEW_RULE_VERSION
    _expected_schema_version = 2

    def __init__(self, factory, stager, *, scope_resolver, extractor=None, **kwargs):
        _require_catalog(factory)
        super().__init__(factory, CanonicalPublicationSourceLoader(factory, stager),
            extractor if extractor is not None else CanonicalNativePublicationExtractor(),
            asset_stager=stager, **kwargs)
        self._native_artifacts = NativeTableArtifactRepository()
        self._authority = DurableWorkKernel(factory,
            HandlerRegistry((self.registered_handler(),)), scope_resolver=scope_resolver)

    @classmethod
    def validate_input(cls, input_ref, expected_state):
        if (expected_state.get("handler_version") != "v2"
                or type(expected_state.get("schema_version")) is not int):
            raise JobExpectedStateConflictError("Native preview version binding is invalid")
        legacy_shape = dict(expected_state)
        legacy_shape.pop("handler_version")
        super().validate_input(input_ref, legacy_shape)

    def _assert_source_binding(self, connection, job, *, allow_queued=False):
        self.validate_input(job.input_ref, job.expected_state)
        if (job.handler_version != "v2" or job.job_type != "publication_preview.extract"
                or job.actor_user_id is None or job.operation_id is None):
            raise JobExpectedStateConflictError("Native preview identity is invalid")
        ids = publication_artifact_ids(job.actor_user_id, job.operation_id)
        if (ids.artifact_id != job.input_ref["artifact_id"]
                or publication_preview_id(job.actor_user_id, job.operation_id) != job.input_ref["preview_id"]):
            raise JobExpectedStateConflictError("Native preview source ownership differs")
        preview = self._previews.require_for_actor(connection,
            preview_id=job.input_ref["preview_id"], actor_user_id=job.actor_user_id)
        source = connection.execute(
            "SELECT raw_sha256,raw_size_bytes,media_type,publisher_user_id,body_retention_status "
            "FROM source_artifact WHERE id=?", (ids.artifact_id,)).fetchone()
        expected = job.expected_state
        if (preview.status not in ({"queued", "extracting"} if allow_queued else {"extracting"})
                or preview.artifact_id != ids.artifact_id or preview.durable_job_id != job.id
                or preview.rule_version != self._rule_version
                or source is None or tuple(source) != (expected["raw_sha256"],
                    expected["raw_size_bytes"], expected["media_type"], job.actor_user_id, "present")
                or connection.execute("SELECT 1 FROM extraction_snapshot WHERE artifact_id=?",
                    (ids.artifact_id,)).fetchone() is not None):
            raise JobExpectedStateConflictError("Native preview source is no longer current")

    def _preflight_source_read(self, job, *, allow_queued=False):
        if job.claim_owner is None:
            raise JobExpectedStateConflictError("Native preview has no active claim")
        connection = self._factory.open(read_only=True)
        try:
            with transaction(connection, TransactionMode.DEFERRED):
                current = self._jobs.assert_effect_fence(connection,
                    fence=ClaimFence(job.id, job.claim_owner, job.attempt_count),
                    now_ms=db_now_ms(connection))
                # A heartbeat may change expiry/progress, never these identities.
                fields = ("actor_user_id", "operation_id", "space_id", "handler_version",
                    "job_type", "input_ref_json", "expected_state_json")
                if any(getattr(current, key) != getattr(job, key) for key in fields):
                    raise JobExpectedStateConflictError("Native preview job record changed")
                self._authority.revalidate_job_authority(connection, current.id)
                self._assert_source_binding(connection, current, allow_queued=allow_queued)
        finally:
            connection.close()

    def _mark_extracting(self, job):
        self._preflight_source_read(job, allow_queued=True)
        super()._mark_extracting(job)
        self._preflight_source_read(job)

    def revalidate_expected_state(self, connection, job):
        self._assert_source_binding(connection, job)

    def commit_effect(self, connection, job, prepared):
        if type(prepared.payload) is not _PreparedNativePreviewExtraction:
            raise PermanentJobExecutionError("Native preview payload is invalid")
        spec = prepared.payload.prepared.extraction
        expected = {"preview_id": job.input_ref["preview_id"], "schema_version": 2,
            "source_snapshot_sha256": spec.extracted_sha256,
            "status": "ready" if spec.status == "ready" else "failed"}
        # JSON identity, not Python's True==1 / 2.0==2 equality aliases.
        try:
            valid = json.dumps(dict(prepared.result), sort_keys=True, allow_nan=False) == json.dumps(expected, sort_keys=True)
        except (TypeError, ValueError):
            valid = False
        if not valid:
            raise PermanentJobExecutionError("Native preview result binding differs")
        super().commit_effect(connection, job, prepared)

    def _prepare_payload(self, *, loaded, outcome, prepared, embedded_assets, title):
        kind = Path(loaded.original_filename or "").suffix.lower()
        native_format = kind in {".docx", ".pptx"}
        if native_format != (outcome.extractor_version == NATIVE_EXTRACTOR_VERSION):
            raise PermanentJobExecutionError("Native source extractor version differs")
        native = None
        if native_format and prepared.extraction.status == "ready":
            native = prepare_native_table_extraction(filename=loaded.original_filename,
                raw_content=loaded.content, extraction=prepared.extraction,
                embedded_assets=embedded_assets)
        return _PreparedNativePreviewExtraction(prepared=prepared, title=title,
            failure_code=outcome.failure_code, embedded_assets=embedded_assets, native=native)

    def _store_extraction(self, connection, *, ids, payload):
        if type(payload) is not _PreparedNativePreviewExtraction:
            raise PermanentJobExecutionError("Native preview payload is invalid")
        spec = payload.prepared.extraction
        required = spec.status == "ready" and spec.extractor_version == NATIVE_EXTRACTOR_VERSION
        if required != (payload.native is not None):
            raise PermanentJobExecutionError("Native preview structure is missing or unexpected")
        try:
            if payload.native is None:
                super()._store_extraction(connection, ids=ids, payload=payload)
            else:
                if (payload.native.extraction != spec
                        or payload.native.embedded_assets != payload.embedded_assets):
                    raise PermanentJobExecutionError("Native preview bundle differs")
                self._native_artifacts.put_source_extraction(connection,
                    artifact_id=ids.artifact_id, extraction_snapshot_id=ids.extraction_snapshot_id,
                    prepared=payload.native)
        except (ArtifactBundleConflictError, ArtifactBundleIntegrityError) as exc:
            # The kernel rolls back this entire transaction before recording failure.
            raise PermanentJobExecutionError("Native extraction storage was rejected") from exc


__all__ = ["NativePublicationPreviewService", "NativePublicationPreviewExtractionHandler"]

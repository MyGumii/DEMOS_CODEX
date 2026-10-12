"""Opt-in v3 Office preview worker: native source plus exact rendered pages.

The historical v2 handler continues to fail closed on active visuals. This
worker only accepts a registered v55 request and commits native extraction,
v53 original/derivative provenance, v54 CAS page archive, then ready preview
under the kernel's one writer transaction. It does not author/publish a Wiki.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from hashlib import sha256

from ...document_source_tables import SourceTableError
from ..migrations.office_hybrid_preview_catalog import OFFICE_HYBRID_PREVIEW_MIGRATION_CATALOG
from ..migrations.v055_office_hybrid_preview import RULE
from ..office_hybrid_source import (
    PreparedOfficeHybridSource, prepare_authorized_office_hybrid_source,
)
from ..office_visual_source import OfficeVisualSourceError
from ..office_visual_windows import OfficeApplicationUnavailableError
from ..protected_sources import (
    PolicyProtectedSourceClassifier, classify_protected_container,
    classify_protected_for_actor,
)
from ..repositories.artifacts import ArtifactBundleConflictError, ArtifactBundleIntegrityError
from ..repositories.office_hybrid_visual_archive import (
    OfficeHybridArchiveConflict, OfficeHybridVisualArchiveRepository,
    PreparedOfficeHybridArchive,
)
from ..repositories.office_visual_derivative import OfficeVisualDerivativeRepository
from ..worker import (
    JobExpectedStateConflictError, PermanentJobExecutionError,
    PreparedJobEffect, _log_10521_failure_diagnostic,
)
from .native_publication import (
    NativePublicationPreviewExtractionHandler, _PreparedNativePreviewExtraction,
)
from .native_rich_publication import CanonicalNativePublicationExtractor
from .office_hybrid_preview import OfficeHybridUploadPolicy
from .publication import (
    ExtractedPublicationContent,
    PublicationPreviewExtractionHandler, publication_artifact_ids,
    publication_preview_id,
)


@dataclass(frozen=True, slots=True)
class _HybridExtractedContent(ExtractedPublicationContent):
    visual_source: PreparedOfficeHybridSource | None = field(default=None, repr=False)
    diagnostic_error: BaseException | None = field(default=None, repr=False)


class CanonicalOfficeHybridExtractor:
    def __init__(self, renderer, *, python_executable, render_policy,
                 policy_protection_classifier: PolicyProtectedSourceClassifier | None = None):
        if not callable(getattr(renderer, "render_for_actor", None)):
            raise TypeError("An approved Office renderer is required.")
        if (policy_protection_classifier is not None
                and not callable(getattr(policy_protection_classifier, "classify_for_actor", None))):
            raise TypeError("The Office policy classifier is invalid.")
        self.renderer = renderer
        self.python_executable = python_executable
        self.render_policy = render_policy
        self.policy_protection_classifier = policy_protection_classifier
        self.native = CanonicalNativePublicationExtractor()

    def extract(self, content, *, media_type, original_filename):
        raise OfficeVisualSourceError("Office hybrid extraction requires a bound actor.")

    def extract_for_actor(self, content, *, media_type, original_filename,
            actor_user_id):
        if (type(original_filename) is not str
                or not original_filename.lower().endswith((".docx", ".pptx"))
                or classify_protected_container(original_filename, content) is not None):
            raise OfficeVisualSourceError(
                "Protected or unsupported Office source requires another authorized lane.")
        if self.policy_protection_classifier is not None:
            try:
                protected_kind = classify_protected_for_actor(
                    original_filename, content, actor_user_id=actor_user_id,
                    policy_classifier=self.policy_protection_classifier,
                )
            except Exception:
                return _HybridExtractedContent(
                    None, "protected_source_classification_failed",
                    "canonical-protected-source", "rich-source-v4-native-tables",
                )
            if protected_kind is not None:
                return _HybridExtractedContent(
                    None, "protected_source_classification_changed",
                    "canonical-protected-source", "rich-source-v4-native-tables",
                )
        try:
            visual = prepare_authorized_office_hybrid_source(
                original_filename, content, actor_user_id=actor_user_id,
                renderer=self.renderer, python_executable=self.python_executable,
                render_policy=self.render_policy)
            native = self.native.extract_native_source(content,
                media_type=media_type, original_filename=original_filename,
                verified_visual_source=visual, actor_user_id=actor_user_id)
        except (OfficeVisualSourceError, SourceTableError) as exc:
            return _HybridExtractedContent(None,
                "office_application_unavailable" if isinstance(exc, OfficeApplicationUnavailableError)
                    else "office_hybrid_source_unavailable",
                "canonical-office-hybrid", "rich-source-v4-native-tables",
                warnings=("office_visual_source_error" if isinstance(exc, OfficeVisualSourceError)
                    else "source_table_error",), diagnostic_error=exc)
        result = native.extraction
        return _HybridExtractedContent(result.markdown, result.failure_code,
            result.extractor_name, result.extractor_version,
            warnings=result.warnings, assets=result.assets,
            visual_source=visual)


@dataclass(frozen=True, slots=True)
class _PreparedOfficeHybridPreviewExtraction(_PreparedNativePreviewExtraction):
    visual_source: PreparedOfficeHybridSource | None = field(default=None, repr=False)
    archive: PreparedOfficeHybridArchive | None = field(default=None, repr=False)
    diagnostic_error: BaseException | None = field(default=None, repr=False)


class OfficeHybridPreviewExtractionHandler(NativePublicationPreviewExtractionHandler):
    _handler_version = "v3"
    _rule_version = RULE
    _expected_schema_version = 3

    def __init__(self, factory, stager, *, scope_resolver, renderer,
            python_executable, export_authorizer, authorization_method,
            policy: OfficeHybridUploadPolicy,
            policy_protection_classifier: PolicyProtectedSourceClassifier | None = None):
        expected = OFFICE_HYBRID_PREVIEW_MIGRATION_CATALOG.migrations
        if factory.migration_catalog.migrations[:len(expected)] != expected:
            raise ValueError("Office hybrid worker requires its verified v55 catalog.")
        if type(policy) is not OfficeHybridUploadPolicy:
            raise TypeError("Office hybrid worker requires a server-owned policy.")
        self.policy = policy
        extractor = CanonicalOfficeHybridExtractor(renderer,
            python_executable=python_executable, render_policy=policy.render,
            policy_protection_classifier=policy_protection_classifier)
        super().__init__(factory, stager, scope_resolver=scope_resolver,
            extractor=extractor)
        self.derivatives = OfficeVisualDerivativeRepository(
            export_authorizer=export_authorizer,
            authorization_method=authorization_method)
        self.archives = OfficeHybridVisualArchiveRepository(factory, stager,
            export_authorizer=export_authorizer)

    @classmethod
    def validate_input(cls, input_ref, expected_state):
        if (expected_state.get("handler_version") != "v3"
                or type(expected_state.get("schema_version")) is not int
                or expected_state["schema_version"] != 3):
            raise JobExpectedStateConflictError("Office hybrid preview version binding is invalid.")
        legacy_shape = dict(expected_state)
        legacy_shape.pop("handler_version")
        PublicationPreviewExtractionHandler.validate_input.__func__(
            cls, input_ref, legacy_shape)

    def _assert_source_binding(self, connection, job, *, allow_queued=False):
        self.validate_input(job.input_ref, job.expected_state)
        if (job.handler_version != "v3" or job.job_type != "publication_preview.extract"
                or job.actor_user_id is None or job.operation_id is None):
            raise JobExpectedStateConflictError("Office hybrid preview identity is invalid.")
        ids = publication_artifact_ids(job.actor_user_id, job.operation_id)
        preview_id = publication_preview_id(job.actor_user_id, job.operation_id)
        if (ids.artifact_id != job.input_ref["artifact_id"]
                or preview_id != job.input_ref["preview_id"]):
            raise JobExpectedStateConflictError("Office hybrid source ownership differs.")
        preview = self._previews.require_for_actor(connection,
            preview_id=preview_id, actor_user_id=job.actor_user_id)
        source = connection.execute("""SELECT raw_sha256,raw_size_bytes,media_type,
            publisher_user_id,body_retention_status FROM source_artifact WHERE id=?""",
            (ids.artifact_id,)).fetchone()
        request = connection.execute("""SELECT q.parent_job_id,q.actor_user_id,q.artifact_id,
            q.rule_version,q.policy_sha256,q.policy_json
            FROM office_hybrid_upload_request q WHERE q.preview_id=?""",
            (preview_id,)).fetchone()
        encoded = self.policy.encoded()
        expected = job.expected_state
        if (preview.status not in ({"queued", "extracting"} if allow_queued else {"extracting"})
                or preview.artifact_id != ids.artifact_id
                or preview.durable_job_id != job.id
                or preview.rule_version != RULE
                or source is None
                or tuple(source) != (expected["raw_sha256"], expected["raw_size_bytes"],
                    expected["media_type"], job.actor_user_id, "present")
                or request is None
                or tuple(request) != (job.id, job.actor_user_id, ids.artifact_id,
                    RULE, sha256(encoded.encode()).hexdigest(), encoded)
                or connection.execute("SELECT 1 FROM extraction_snapshot WHERE artifact_id=?",
                    (ids.artifact_id,)).fetchone() is not None):
            raise JobExpectedStateConflictError("Office hybrid preview source is no longer current.")

    def _prepare_payload(self, *, loaded, outcome, prepared, embedded_assets, title):
        if type(outcome) is not _HybridExtractedContent:
            raise PermanentJobExecutionError("Office hybrid extractor result is invalid.")
        native = NativePublicationPreviewExtractionHandler._prepare_payload(self,
            loaded=loaded, outcome=outcome, prepared=prepared,
            embedded_assets=embedded_assets, title=title)
        if ((prepared.extraction.status == "ready")
                != (outcome.visual_source is not None and native.native is not None)):
            raise PermanentJobExecutionError("Office hybrid input evidence is incomplete.")
        return _PreparedOfficeHybridPreviewExtraction(
            prepared=native.prepared, title=native.title,
            failure_code=native.failure_code,
            embedded_assets=native.embedded_assets,
            protected_provenance=native.protected_provenance,
            native=native.native, visual_source=outcome.visual_source,
            diagnostic_error=outcome.diagnostic_error)

    def prepare(self, job):
        base = super().prepare(job)
        payload = base.payload
        if type(payload) is not _PreparedOfficeHybridPreviewExtraction:
            if base.cleanup is not None:
                base.cleanup()
            raise PermanentJobExecutionError("Office hybrid preview payload is invalid.")
        if payload.diagnostic_error is not None:
            # The typed cause remains process-local. The existing opt-in
            # logger reads class/trusted code locations only, never messages,
            # arguments, locals, document bytes or source paths.
            _log_10521_failure_diagnostic(job.id, payload.diagnostic_error)
            payload = replace(payload, diagnostic_error=None)
            base = replace(base, payload=payload)
        if payload.prepared.extraction.status != "ready":
            return base
        source_lease, archive = None, None
        try:
            source_lease = self._loader.load(
                artifact_id=job.input_ref["artifact_id"],
                expected_sha256=job.expected_state["raw_sha256"],
                expected_size=job.expected_state["raw_size_bytes"],
                expected_media_type=job.expected_state["media_type"])
            ids = publication_artifact_ids(job.actor_user_id, job.operation_id)
            archive = self.archives.prepare(payload.visual_source,
                preview_id=job.input_ref["preview_id"],
                artifact_id=ids.artifact_id,
                extraction_snapshot_id=ids.extraction_snapshot_id,
                actor_user_id=job.actor_user_id,
                original_filename=source_lease.original_filename,
                original=source_lease.content,
                created_at_ms=payload.prepared.extraction.created_at_ms)
            bound = replace(payload, archive=archive)

            def close():
                try:
                    archive.close()
                finally:
                    try:
                        source_lease.lease.close()
                    finally:
                        if base.cleanup is not None:
                            base.cleanup()

            return PreparedJobEffect(base.result, bound, close)
        except BaseException:
            if archive is not None:
                archive.close()
            if source_lease is not None:
                source_lease.lease.close()
            if base.cleanup is not None:
                base.cleanup()
            raise

    def commit_effect(self, connection, job, prepared):
        payload = prepared.payload
        if type(payload) is not _PreparedOfficeHybridPreviewExtraction:
            raise PermanentJobExecutionError("Office hybrid preview payload is invalid.")
        spec = payload.prepared.extraction
        expected = {"preview_id": job.input_ref["preview_id"], "schema_version": 3,
            "source_snapshot_sha256": spec.extracted_sha256,
            "status": "ready" if spec.status == "ready" else "failed"}
        if dict(prepared.result) != expected:
            raise PermanentJobExecutionError("Office hybrid preview result differs.")
        PublicationPreviewExtractionHandler.commit_effect(self, connection, job, prepared)

    def _store_extraction(self, connection, *, ids, payload):
        if type(payload) is not _PreparedOfficeHybridPreviewExtraction:
            raise PermanentJobExecutionError("Office hybrid extraction payload is invalid.")
        spec = payload.prepared.extraction
        if spec.status != "ready":
            PublicationPreviewExtractionHandler._store_extraction(
                self, connection, ids=ids, payload=payload)
            return
        if payload.native is None or payload.visual_source is None or payload.archive is None:
            raise PermanentJobExecutionError("Office hybrid extraction is incomplete.")
        if (payload.native.extraction != spec
                or payload.native.embedded_assets != payload.embedded_assets):
            raise PermanentJobExecutionError("Office native and visual extraction differ.")
        try:
            self._native_artifacts.put_source_extraction(connection,
                artifact_id=ids.artifact_id,
                extraction_snapshot_id=ids.extraction_snapshot_id,
                prepared=payload.native)
            self.derivatives.retain(connection,
                preview_id=payload.archive.metadata[0],
                artifact_id=ids.artifact_id,
                extraction_snapshot_id=ids.extraction_snapshot_id,
                actor_user_id=payload.archive.metadata[1],
                original_filename=payload.visual_source.render.original_filename,
                original=payload.archive.original,
                prepared=payload.visual_source,
                created_at_ms=spec.created_at_ms)
            self.archives.put(connection, payload.archive)
        except (ArtifactBundleConflictError, ArtifactBundleIntegrityError,
                OfficeHybridArchiveConflict, OfficeVisualSourceError,
                PermissionError) as exc:
            raise PermanentJobExecutionError(
                "Office hybrid extraction storage was rejected.") from exc


__all__ = ["CanonicalOfficeHybridExtractor", "OfficeHybridPreviewExtractionHandler"]

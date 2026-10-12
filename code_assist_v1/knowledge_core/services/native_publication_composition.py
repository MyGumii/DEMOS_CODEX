"""Explicit native/display web/worker composition; no import-time activation.

Office uploads use source-bound native tables, with opt-in PPT display. Other supported formats retain
the detailed authoring-only path. Web and worker construct this same registry;
only a worker runs its kernel. Deployment/forward migration remains separate.
"""
from dataclasses import dataclass
import json
from pathlib import Path

from ..db import transaction, TransactionMode
from ..migrations.native_display_publication_catalog import NATIVE_DISPLAY_PUBLICATION_MIGRATION_CATALOG
from ..migrations.pptx_role_publication_catalog import PPTX_ROLE_PUBLICATION_MIGRATION_CATALOG
from ..migrations.legacy_authoring_retention_catalog import LEGACY_AUTHORING_RETENTION_MIGRATION_CATALOG
from ..migrations.pdf_publication_catalog import PDF_PUBLICATION_MIGRATION_CATALOG
from ..migrations.pdf_visual_publication_catalog import PDF_VISUAL_PUBLICATION_MIGRATION_CATALOG
from ..migrations.pdf_visual_writing_policy_catalog import PDF_VISUAL_WRITING_POLICY_MIGRATION_CATALOG
from ..migrations.pdf_evidence_units_catalog import PDF_EVIDENCE_UNITS_MIGRATION_CATALOG
from ..migrations.native_semantic_targets_catalog import NATIVE_SEMANTIC_TARGETS_MIGRATION_CATALOG
from ..migrations.semantic_rename_reseed_catalog import SEMANTIC_RENAME_RESEED_MIGRATION_CATALOG
from ..migrations.protected_extraction_provenance_catalog import PROTECTED_EXTRACTION_PROVENANCE_MIGRATION_CATALOG
from ..migrations.protected_pdf_visual_binding_catalog import PROTECTED_PDF_VISUAL_BINDING_MIGRATION_CATALOG
from ..migrations.temporal_root_rebinding_catalog import TEMPORAL_ROOT_REBINDING_MIGRATION_CATALOG
from ..migrations.office_hybrid_publication_body_catalog import OFFICE_HYBRID_PUBLICATION_BODY_MIGRATION_CATALOG
from ..migrations.office_personal_writer_part_catalog import OFFICE_PERSONAL_WRITER_PART_MIGRATION_CATALOG
from ..migrations.company_policy_provenance_catalog import COMPANY_POLICY_PROVENANCE_MIGRATION_CATALOG
from ..migrations.pdf_owned_evidence_prompt_catalog import PDF_OWNED_EVIDENCE_PROMPT_MIGRATION_CATALOG
from ..migrations.pdf_fenced_response_snapshot_catalog import PDF_FENCED_RESPONSE_SNAPSHOT_MIGRATION_CATALOG
from ..migrations.pdf_section_writing_catalog import PDF_SECTION_WRITING_MIGRATION_CATALOG
from ..migrations.pdf_section_json_output_catalog import PDF_SECTION_JSON_OUTPUT_MIGRATION_CATALOG
from ..migrations.pdf_section_guard_dispatch_catalog import PDF_SECTION_GUARD_DISPATCH_MIGRATION_CATALOG
from ..migrations.pdf_section_schema_output_catalog import PDF_SECTION_SCHEMA_OUTPUT_MIGRATION_CATALOG
from ..migrations.pdf_section_markdown_output_catalog import PDF_SECTION_MARKDOWN_OUTPUT_MIGRATION_CATALOG
from ..migrations.pdf_section_markdown_delimiters_catalog import PDF_SECTION_MARKDOWN_DELIMITERS_MIGRATION_CATALOG
from ..migrations.pdf_batch_markdown_catalog import PDF_BATCH_MARKDOWN_MIGRATION_CATALOG
from ..migrations.pdf_source_faithful_markdown_catalog import PDF_SOURCE_FAITHFUL_MARKDOWN_MIGRATION_CATALOG
from ..migrations.pdf_layout_retention_catalog import PDF_LAYOUT_RETENTION_MIGRATION_CATALOG
from ..migrations.pdf_layout_math_format_catalog import PDF_LAYOUT_MATH_FORMAT_MIGRATION_CATALOG
from ..migrations.pdf_layout_subject_binding_catalog import PDF_LAYOUT_SUBJECT_BINDING_MIGRATION_CATALOG
from ..migrations.pdf_layout_nonblank_groups_catalog import PDF_LAYOUT_NONBLANK_GROUPS_MIGRATION_CATALOG
from ..migrations.pdf_layout_literal_appendix_catalog import PDF_LAYOUT_LITERAL_APPENDIX_MIGRATION_CATALOG
from ..migrations.pdf_figure_roi_catalog import PDF_FIGURE_ROI_MIGRATION_CATALOG
from ..migrations.pdf_curve_domain_catalog import PDF_CURVE_DOMAIN_MIGRATION_CATALOG
from ..migrations.pdf_nonplot_writing_catalog import PDF_NONPLOT_WRITING_MIGRATION_CATALOG
from ..migrations.pdf_nonthinking_writing_catalog import PDF_NONTHINKING_WRITING_MIGRATION_CATALOG
from ..migrations.pdf_curve_array_guidance_catalog import PDF_CURVE_ARRAY_GUIDANCE_MIGRATION_CATALOG
from ..migrations.pdf_text_output_headroom_catalog import PDF_TEXT_OUTPUT_HEADROOM_MIGRATION_CATALOG
from ..migrations.pdf_finite_text_writing_catalog import PDF_FINITE_TEXT_WRITING_MIGRATION_CATALOG
from ..migrations.pdf_bounded_text_partition_catalog import PDF_BOUNDED_TEXT_PARTITION_MIGRATION_CATALOG
from ..migrations.pdf_curve_named_bounds_catalog import PDF_CURVE_NAMED_BOUNDS_MIGRATION_CATALOG
from ..migrations.pdf_curve_prompt_alignment_catalog import PDF_CURVE_PROMPT_ALIGNMENT_MIGRATION_CATALOG
from ..migrations.pdf_visual_contract_alignment_catalog import PDF_VISUAL_CONTRACT_ALIGNMENT_MIGRATION_CATALOG
from ..migrations.pdf_text_json_contract_alignment_catalog import PDF_TEXT_JSON_CONTRACT_ALIGNMENT_MIGRATION_CATALOG
from ..migrations.pdf_native_frame_contract_catalog import PDF_NATIVE_FRAME_CONTRACT_MIGRATION_CATALOG
from ..migrations.pdf_complete_source_packing_catalog import PDF_COMPLETE_SOURCE_PACKING_MIGRATION_CATALOG
from ..migrations.pdf_native_categorical_axes_catalog import PDF_NATIVE_CATEGORICAL_AXES_MIGRATION_CATALOG
from ..migrations.pdf_native_tick_conditions_catalog import PDF_NATIVE_TICK_CONDITIONS_MIGRATION_CATALOG
from ..migrations.pdf_v10_literal_tables_catalog import PDF_V10_LITERAL_TABLES_MIGRATION_CATALOG
from ..migrations.pdf_v10_chart_tables_catalog import PDF_V10_CHART_TABLES_MIGRATION_CATALOG
from ..migrations.pdf_chart_labels_catalog import PDF_CHART_LABELS_MIGRATION_CATALOG
from ..migrations.pdf_numeric_append_catalog import PDF_NUMERIC_APPEND_MIGRATION_CATALOG
from ..migrations.pdf_atomic_request_partition_catalog import PDF_ATOMIC_REQUEST_PARTITION_MIGRATION_CATALOG
from ..migrations.pdf_context_resume_catalog import PDF_CONTEXT_RESUME_MIGRATION_CATALOG
from ..migrations.pdf_terminal_result_reuse_catalog import PDF_TERMINAL_RESULT_REUSE_MIGRATION_CATALOG
from ..migrations.v032_native_display_storage import DISPLAY_SOURCE_POLICY
from ..wiki_authoring import _hash, _json
from ..worker import DurableWorkKernel,HandlerRegistry
from ..wiki_preservation import AUTHORING_ONLY_VERSIONS
from ..protected_sources import (
    PolicyProtectedSourceClassifier, ProtectedSourceKind,
    classify_protected_container, classify_protected_for_actor,
)
from ..protected_pdf_open import AuthorizedOwnerPdfOpener
from ..protected_source_snapshot import AuthorizedProtectedSourceOpener
from .cross_kind_publication import CrossKindPublicationService,CrossKindPublicationSelector
from .native_authoring_workflow import NativeAuthoringWorkflow,NativeAuthoringPreviewService,NativeUploadPolicy
from .native_publication import NativePublicationPreviewExtractionHandler
from .publication import (PublicationPreviewService,PublicationPreviewExtractionHandler,
    PublicationApiService,CanonicalPublicationSourceLoader,CreateUploadPreviewCommand,
    publication_preview_id,_preview_user,_preview_epoch,_publication_target,
    PublicationPreviewIdempotencyError, PublicationPreviewStagingError,
    PublicationPreviewAuthorizationError)
from .rich_publication import CanonicalRichPublicationExtractor
from .wiki_preservation import WikiPreservationWorkflow,WikiPreservationUploadPolicy
from .pptx_role_authoring_workflow import PptxRoleAuthoringWorkflow,PptxRoleAuthoringPreviewService,PptxRoleUploadPolicy
from .pptx_role_publication import PptxRolePublicationService,PptxRolePublicationSelector
from .pdf_authoring_workflow import PdfAuthoringWorkflow,PdfAuthoringPreviewService,PdfUploadPolicy
from .pdf_publication import PdfPublicationService,PdfPublicationSelector
from .pdf_visual_authoring_workflow import PdfVisualAuthoringWorkflow, PdfVisualAuthoringPreviewService, PdfVisualUploadPolicy
from .pdf_visual_publication import PdfVisualPublicationService, PdfVisualPublicationSelector
from .office_hybrid_preview import OfficeHybridPreviewService, OfficeHybridUploadPolicy, OfficeHybridUploadRegistration
from .office_hybrid_extraction import OfficeHybridPreviewExtractionHandler
from .office_hybrid_authoring_source import CanonicalOfficeHybridAuthoringSourceService
from .office_hybrid_publication_source import OfficeHybridPublicationSourceService
from .office_hybrid_publication import OfficeHybridPublicationService, OfficeHybridPublicationSelector
from .office_hybrid_wiki_start import OfficeHybridWikiWriteService
from .office_hybrid_wiki_dispatch import OfficeHybridWikiDispatchHandler
from .office_hybrid_wiki_worker import OfficeHybridWikiWriteHandler
from ..native_wiki_batches import NativeWikiBatchAuthor
from ..office_hybrid_visual_authoring import OfficeHybridVisualAuthor


class _RetainedNativePreview(NativeAuthoringPreviewService):
    """Replay only, using permanent server-owned policy identity, never its body."""

    def __init__(self, *args, retained_identity, **kwargs):
        super().__init__(*args, **kwargs)
        self._retained_identity = retained_identity

    def _upload_request_sha256(self, **fields):
        original = PublicationPreviewService._upload_request_sha256(self, **fields)
        version, policy_sha256 = self._retained_identity
        return _hash(_json(dict(legacy_request_sha256=original, version=version,
            handler_version="v2", rule_version=self._upload_rule_version,
            policy_sha256=policy_sha256)))

    def _read_queued_replay(self, **fields):
        result = super()._read_queued_replay(**fields)
        if result is None:
            raise PublicationPreviewIdempotencyError("Retained native upload has no committed request.")
        return result


class _RetainedPptxRolePreview(PptxRoleAuthoringPreviewService):
    """Exact old role upload replay; no dependency on private policy retention."""

    def __init__(self, *args, retained_identity, **kwargs):
        super().__init__(*args, **kwargs)
        self._retained_identity = retained_identity

    def _upload_request_sha256(self, **fields):
        original = PublicationPreviewService._upload_request_sha256(self, **fields)
        version, policy_sha256 = self._retained_identity
        return _hash(_json(dict(legacy_request_sha256=original, version=version,
            handler_version="v2", rule_version=self._upload_rule_version,
            policy_sha256=policy_sha256)))

    def _read_queued_replay(self, **fields):
        result = super()._read_queued_replay(**fields)
        if result is None:
            raise PublicationPreviewIdempotencyError("Retained role upload has no committed request.")
        return result


class _RetainedPdfPreview(PdfAuthoringPreviewService):
    """Replay registered PDF identity without private policy or new adoption."""

    def __init__(self, *args, retained_identity, **kwargs):
        super().__init__(*args, **kwargs)
        self._retained_identity = retained_identity

    def create_upload_preview(self, command):
        # Common request validation/hash detects even a changed extension. This
        # object cannot enqueue: _read_queued_replay requires a committed match.
        return PublicationPreviewService.create_upload_preview(self, command)

    def _upload_request_sha256(self, **fields):
        original = PublicationPreviewService._upload_request_sha256(self, **fields)
        version, policy_sha256 = self._retained_identity
        return _hash(_json(dict(legacy_request_sha256=original, version=version,
            handler_version="v1", rule_version=self._upload_rule_version,
            policy_sha256=policy_sha256)))

    def _read_queued_replay(self, **fields):
        result = super()._read_queued_replay(**fields)
        if result is None:
            raise PublicationPreviewIdempotencyError("Retained PDF upload has no committed request.")
        return result


class _RetainedPdfVisualPreview(PdfVisualAuthoringPreviewService):
    """Exact visual upload replay using permanent policy identity only."""

    def __init__(self, *args, retained_identity, **kwargs):
        super().__init__(*args, **kwargs)
        self._retained_identity = retained_identity

    create_upload_preview = _RetainedPdfPreview.create_upload_preview
    _upload_request_sha256 = _RetainedPdfPreview._upload_request_sha256

    def _read_queued_replay(self, **fields):
        result = PublicationPreviewService._read_queued_replay(self, **fields)
        if result is None:
            raise PublicationPreviewIdempotencyError("Retained visual PDF upload has no committed request.")
        return result


class _RetainedOfficePreview(OfficeHybridPreviewService):
    """Replay a registered v3 upload using its original policy hash only."""

    def __init__(self, *args, retained_policy_sha256, **kwargs):
        super().__init__(*args, **kwargs)
        self._retained_policy_sha256 = retained_policy_sha256

    def _upload_request_sha256(self, **fields):
        original = PublicationPreviewService._upload_request_sha256(self, **fields)
        return _hash(_json(dict(legacy_request_sha256=original,
            handler_version="v3", rule_version=self._upload_rule_version,
            policy_sha256=self._retained_policy_sha256)))

    def _read_queued_replay(self, **fields):
        result = PublicationPreviewService._read_queued_replay(self, **fields)
        if result is None:
            raise PublicationPreviewIdempotencyError("Retained Office upload has no committed request.")
        return result


class _ProtectedPdfVisualExtractor:
    """Permit image-only evidence only for an already-registered visual job.

    The v1 handler checks that registration before calling this separate method;
    ordinary/retained v1 previews still receive the fail-closed extraction.
    """

    def __init__(self, base: CanonicalRichPublicationExtractor):
        self._base = base

    def extract(self, content, *, media_type, original_filename):
        return self._base.extract(content, media_type=media_type,
            original_filename=original_filename)

    def extract_for_actor(self, content, *, media_type, original_filename, actor_user_id,
                          expected_protected_kind=None):
        return self._base.extract_for_actor(content, media_type=media_type,
            original_filename=original_filename, actor_user_id=actor_user_id,
            expected_protected_kind=expected_protected_kind)

    def extract_for_visual_preview(self, content, *, media_type, original_filename,
                                   actor_user_id):
        return self._base.extract(content, media_type=media_type,
            original_filename=original_filename,
            allow_unexamined_protected_pdf_visual=True,
            actor_user_id=actor_user_id)


class _PolicyProtectedPreview(PublicationPreviewService):
    """Persist the web provider's policy signal in the queued v1 job."""

    def _upload_expected_state(self, state):
        return {**super()._upload_expected_state(state),
                "protected_source_kind": ProtectedSourceKind.COMPANY_POLICY_PROTECTED.value}

    def _read_queued_replay(self, **fields):
        result = super()._read_queued_replay(**fields)
        if result is None:
            return None
        connection = self._factory.open(read_only=True)
        try:
            row = connection.execute(
                """SELECT j.expected_state_json FROM publication_preview p
                     JOIN durable_job j ON j.id=p.durable_job_id
                    WHERE p.id=? AND p.actor_user_id=?""",
                (result.preview_id, fields["actor_id"]),
            ).fetchone()
            try:
                expected = json.loads(row[0]) if row is not None else None
            except (TypeError, ValueError):
                expected = None
            if (type(expected) is not dict
                    or expected.get("protected_source_kind")
                    != ProtectedSourceKind.COMPANY_POLICY_PROTECTED.value):
                raise PublicationPreviewIdempotencyError(
                    "A retained upload lacks the policy-protected source claim."
                )
            return result
        finally:
            connection.close()


class _FormatRoutedPreviewService(PublicationPreviewService):
    def __init__(self,factory,scope_resolver,stager,*,legacy,native,selector,display=None,pptx_role=None,pdf=None,pdf_visual=None,
            protected_pdf_visual=False,office_registration=None,
            policy_protection_classifier: PolicyProtectedSourceClassifier | None = None):
        super().__init__(factory,scope_resolver,stager,wiki_upload=legacy,wiki_publication=selector)
        self._native_workflow = native
        self._selector = selector
        self._native_preview=NativeAuthoringPreviewService(factory,scope_resolver,stager,
            wiki_upload=native,wiki_publication=selector)
        self._display_preview = (None if display is None else NativeAuthoringPreviewService(
            factory,scope_resolver,stager,wiki_upload=display,wiki_publication=selector))
        self._role_workflow = pptx_role
        self._role_preview = (None if pptx_role is None else PptxRoleAuthoringPreviewService(
            factory,scope_resolver,stager,wiki_upload=pptx_role,wiki_publication=selector))
        self._pdf_workflow = pdf
        self._pdf_preview = (None if pdf is None else PdfAuthoringPreviewService(
            factory,scope_resolver,stager,wiki_upload=pdf,wiki_publication=selector))
        self._pdf_visual_workflow = pdf_visual
        self._pdf_visual_preview = (None if pdf_visual is None else PdfVisualAuthoringPreviewService(
            factory,scope_resolver,stager,wiki_upload=pdf_visual,wiki_publication=selector))
        self._office_registration = office_registration
        self._office_preview = (None if office_registration is None else OfficeHybridPreviewService(
            factory,scope_resolver,stager,registration=office_registration,wiki_publication=selector))
        self._protected_pdf_visual = protected_pdf_visual
        self._policy_protection_classifier = policy_protection_classifier

    def create_upload_preview(self,command):
        protected_kind = None
        policy_classifier = getattr(self, "_policy_protection_classifier", None)
        if isinstance(command, CreateUploadPreviewCommand) and policy_classifier is not None:
            try:
                protected_kind = classify_protected_for_actor(
                    command.original_filename, command.content,
                    actor_user_id=command.actor_user_id,
                    policy_classifier=policy_classifier,
                )
            except PermissionError as exc:
                raise PublicationPreviewAuthorizationError(
                    "The source protection status is not available to this actor."
                ) from exc
            except Exception as exc:
                raise PublicationPreviewStagingError(
                    "The source protection status could not be verified."
                ) from exc
            if protected_kind is ProtectedSourceKind.COMPANY_POLICY_PROTECTED:
                return _PolicyProtectedPreview(self._factory,self._scope,self._stager,
                    wiki_upload=self._wiki_upload,wiki_publication=self._selector
                ).create_upload_preview(command)
        if self._office_preview is not None and isinstance(command, CreateUploadPreviewCommand):
            identity = self._retained_office_identity(command)
            if identity is not None:
                return _RetainedOfficePreview(self._factory,self._scope,self._stager,
                    registration=self._office_registration,wiki_publication=self._selector,
                    retained_policy_sha256=identity).create_upload_preview(command)
        if self._pdf_visual_preview is not None and isinstance(command, CreateUploadPreviewCommand):
            identity = self._retained_pdf_visual_identity(command)
            if identity is not None:
                return _RetainedPdfVisualPreview(self._factory,self._scope,self._stager,
                    wiki_upload=self._pdf_visual_workflow,wiki_publication=self._selector,
                    retained_identity=identity).create_upload_preview(command)
        if self._pdf_preview is not None and isinstance(command, CreateUploadPreviewCommand):
            identity = self._retained_pdf_identity(command)
            if identity is not None:
                return _RetainedPdfPreview(self._factory,self._scope,self._stager,
                    wiki_upload=self._pdf_workflow,wiki_publication=self._selector,
                    retained_identity=identity).create_upload_preview(command)
        if self._role_preview is not None and isinstance(command, CreateUploadPreviewCommand):
            identity = self._retained_role_identity(command)
            if identity is not None:
                return _RetainedPptxRolePreview(self._factory,self._scope,self._stager,
                    wiki_upload=self._role_workflow,wiki_publication=self._selector,
                    retained_identity=identity).create_upload_preview(command)
        if (self._display_preview is not None or self._role_preview is not None) and isinstance(command, CreateUploadPreviewCommand):
            identity = self._retained_native_identity(command)
            if identity is not None:
                return _RetainedNativePreview(self._factory,self._scope,self._stager,
                    wiki_upload=self._native_workflow,wiki_publication=self._selector,
                    retained_identity=identity).create_upload_preview(command)
        if isinstance(command, CreateUploadPreviewCommand) and policy_classifier is None:
            protected_kind = classify_protected_container(
                command.original_filename, command.content)
        if protected_kind is not None:
            if (self._protected_pdf_visual and self._pdf_visual_preview is not None
                    and isinstance(command.original_filename, str)
                    and Path(command.original_filename).suffix.lower() == '.pdf'
                    and not self._is_previous_upload(command)):
                return self._pdf_visual_preview.create_upload_preview(command)
            # A protected container cannot be parsed as native OOXML/PDF
            # geometry.  It needs an explicitly authorized source opener and
            # the extraction-backed preservation lane.  Without that opener,
            # extraction fails closed with a distinct protected-source code.
            return super().create_upload_preview(command)
        # Select a server-owned parser/workflow by format only; actual content,
        # filename and request identities are validated by the chosen producer.
        # No topic, account name or client-supplied generation policy routing.
        if (isinstance(command,CreateUploadPreviewCommand)
                and isinstance(command.original_filename,str)
                and Path(command.original_filename).suffix.lower() in (
                    {'.docx','.pptx','.pdf'} if self._pdf_preview is not None else {'.docx','.pptx'})):
            if self._is_previous_upload(command):
                # Keep the original request hash/result and authorization
                # checks. This does not register a new authoring policy or
                # adopt an old unfinished upload into the successor pipeline.
                return super().create_upload_preview(command)
            if Path(command.original_filename).suffix.lower() == '.pdf':
                return (self._pdf_visual_preview or self._pdf_preview).create_upload_preview(command)
            if self._office_preview is not None:
                return self._office_preview.create_upload_preview(command)
            pptx_preview = self._role_preview or self._display_preview
            selected = (pptx_preview if pptx_preview is not None
                and Path(command.original_filename).suffix.lower() == '.pptx' else self._native_preview)
            return selected.create_upload_preview(command)
        return super().create_upload_preview(command)

    def _retained_office_identity(self, command):
        actor = _preview_user(command.actor_user_id)
        epoch = _preview_epoch(command.actor_scope_epoch)
        target = _publication_target(command.target)
        preview_id = publication_preview_id(actor, command.operation_id)
        connection = self._factory.open(read_only=True)
        try:
            with transaction(connection, TransactionMode.DEFERRED):
                self._authorized_targets(connection,actor_id=actor,epoch=epoch,target=target)
                row = connection.execute("""SELECT q.policy_sha256,q.parent_job_id,
                    p.durable_job_id,p.rule_version,j.handler_version
                    FROM office_hybrid_upload_request q
                    JOIN publication_preview p ON p.id=q.preview_id
                    JOIN durable_job j ON j.id=p.durable_job_id
                    WHERE p.id=? AND p.actor_user_id=?
                      AND j.job_type='publication_preview.extract'""",
                    (preview_id,actor)).fetchone()
                if row is None:
                    return None
                if (row['parent_job_id'] != row['durable_job_id']
                        or row['rule_version'] != self._office_preview._upload_rule_version
                        or row['handler_version'] != 'v3'):
                    raise PublicationPreviewIdempotencyError("Retained Office upload identity changed.")
                return row['policy_sha256']
        finally:
            connection.close()

    def _retained_pdf_visual_identity(self, command):
        actor = _preview_user(command.actor_user_id)
        epoch = _preview_epoch(command.actor_scope_epoch)
        target = _publication_target(command.target)
        preview_id = publication_preview_id(actor, command.operation_id)
        connection = self._factory.open(read_only=True)
        try:
            with transaction(connection, TransactionMode.DEFERRED):
                self._authorized_targets(connection,actor_id=actor,epoch=epoch,target=target)
                row = connection.execute("""SELECT q.policy_sha256,q.parent_job_id,
                    p.durable_job_id,p.rule_version,j.handler_version
                    FROM pdf_visual_workflow_request q JOIN publication_preview p ON p.id=q.preview_id
                    JOIN durable_job j ON j.id=p.durable_job_id
                    WHERE p.id=? AND p.actor_user_id=? AND j.job_type='publication_preview.extract'""",
                    (preview_id,actor)).fetchone()
                if row is None:
                    return None
                if (row['parent_job_id'] != row['durable_job_id']
                        or row['rule_version'] != self._pdf_visual_preview._upload_rule_version
                        or row['handler_version'] != 'v1'):
                    raise PublicationPreviewIdempotencyError("Retained visual PDF upload identity changed.")
                version, _, _ = self._pdf_visual_workflow._request_identity(connection, preview_id)
                return version, row['policy_sha256']
        finally:
            connection.close()

    def _retained_pdf_identity(self, command):
        actor = _preview_user(command.actor_user_id)
        epoch = _preview_epoch(command.actor_scope_epoch)
        target = _publication_target(command.target)
        preview_id = publication_preview_id(actor, command.operation_id)
        connection = self._factory.open(read_only=True)
        try:
            with transaction(connection, TransactionMode.DEFERRED):
                self._authorized_targets(connection,actor_id=actor,epoch=epoch,target=target)
                row = connection.execute("""SELECT q.policy_sha256,q.parent_job_id,
                    p.durable_job_id,p.rule_version,j.handler_version
                    FROM pdf_workflow_request q JOIN publication_preview p ON p.id=q.preview_id
                    JOIN durable_job j ON j.id=p.durable_job_id
                    WHERE p.id=? AND p.actor_user_id=? AND j.job_type='publication_preview.extract'""",
                    (preview_id,actor)).fetchone()
                if row is None:
                    return None
                if (row['parent_job_id'] != row['durable_job_id']
                        or row['rule_version'] != self._pdf_preview._upload_rule_version
                        or row['handler_version'] != 'v1'):
                    raise PublicationPreviewIdempotencyError("Retained PDF upload identity changed.")
                version, _, _ = self._pdf_workflow._request_identity(connection, preview_id)
                return version, row['policy_sha256']
        finally:
            connection.close()

    def _retained_role_identity(self, command):
        actor = _preview_user(command.actor_user_id)
        epoch = _preview_epoch(command.actor_scope_epoch)
        target = _publication_target(command.target)
        preview_id = publication_preview_id(actor, command.operation_id)
        connection = self._factory.open(read_only=True)
        try:
            with transaction(connection, TransactionMode.DEFERRED):
                self._authorized_targets(connection,actor_id=actor,epoch=epoch,target=target)
                row = connection.execute("""SELECT q.policy_sha256,q.parent_job_id,
                    p.durable_job_id,p.rule_version,j.handler_version
                    FROM pptx_role_workflow_request q JOIN publication_preview p ON p.id=q.preview_id
                    JOIN durable_job j ON j.id=p.durable_job_id
                    WHERE p.id=? AND p.actor_user_id=? AND j.job_type='publication_preview.extract'""",
                    (preview_id,actor)).fetchone()
                if row is None:
                    return None
                if (row['parent_job_id'] != row['durable_job_id']
                        or row['rule_version'] != self._role_preview._upload_rule_version
                        or row['handler_version'] != 'v2'):
                    raise PublicationPreviewIdempotencyError("Retained role upload identity changed.")
                version, _, _ = self._role_workflow._request_identity(connection, preview_id)
                return version, row['policy_sha256']
        finally:
            connection.close()

    def _retained_native_identity(self, command):
        actor = _preview_user(command.actor_user_id)
        epoch = _preview_epoch(command.actor_scope_epoch)
        target = _publication_target(command.target)
        preview_id = publication_preview_id(actor, command.operation_id)
        connection = self._factory.open(read_only=True)
        try:
            with transaction(connection, TransactionMode.DEFERRED):
                self._authorized_targets(connection,actor_id=actor,epoch=epoch,target=target)
                row = connection.execute("""SELECT q.policy_sha256,q.parent_job_id,
                    p.durable_job_id,p.rule_version,j.handler_version
                    FROM native_workflow_request q JOIN publication_preview p ON p.id=q.preview_id
                    JOIN durable_job j ON j.id=p.durable_job_id
                    WHERE p.id=? AND p.actor_user_id=? AND j.job_type='publication_preview.extract'""",
                    (preview_id,actor)).fetchone()
                if row is None:
                    return None
                if (row['parent_job_id'] != row['durable_job_id']
                        or row['rule_version'] != self._native_preview._upload_rule_version
                        or row['handler_version'] != 'v2'):
                    raise PublicationPreviewIdempotencyError("Retained native upload identity changed.")
                version, _, _ = self._native_workflow._request_identity(connection, preview_id)
                return version, row['policy_sha256']
        finally:
            connection.close()

    def _is_previous_upload(self,command):
        actor=_preview_user(command.actor_user_id)
        epoch=_preview_epoch(command.actor_scope_epoch)
        target=_publication_target(command.target)
        preview_id=publication_preview_id(actor,command.operation_id)
        connection=self._factory.open(read_only=True)
        try:
            with transaction(connection,TransactionMode.DEFERRED):
                self._authorized_targets(connection,actor_id=actor,epoch=epoch,target=target)
                # Metadata only, after current authorization. The selected
                # producer still verifies the complete request and replay.
                row=connection.execute("""SELECT p.rule_version,j.handler_version
                    FROM publication_preview p JOIN durable_job j ON j.id=p.durable_job_id
                    WHERE p.id=? AND p.actor_user_id=? AND j.job_type='publication_preview.extract'""",
                    (preview_id,actor)).fetchone()
                return row is not None and row['rule_version']==self._upload_rule_version and row['handler_version']=='v1'
        finally:
            connection.close()


@dataclass(frozen=True,slots=True)
class NativePublicationComposition:
    api: PublicationApiService
    kernel: DurableWorkKernel
    office_starter: OfficeHybridWikiWriteService | None = None
    pdf_continuation: object | None = None


def compose_native_publication(factory,scope_resolver,stager,*,native_author,native_policy,
        legacy_author,legacy_policy,additional_handlers=(),display_policy=None,
        pptx_role_author=None,pptx_role_policy=None,preparation_read_binding=None,
        pdf_author=None,pdf_policy=None,pdf_python_executable=None,
        pdf_visual_author=None,pdf_visual_policy=None,pdf_continuation_enabled=False,
        protected_pdf_opener: AuthorizedOwnerPdfOpener | None = None,
        protected_source_opener: AuthorizedProtectedSourceOpener | None = None,
        policy_protection_classifier: PolicyProtectedSourceClassifier | None = None,
        protected_publication_authorizer=None,
        require_protected_publication_authorization=False,
        protected_authoring_authorizer=None,
        require_protected_authoring_authorization=False,
        office_policy: OfficeHybridUploadPolicy | None = None,
        office_renderer=None, office_export_authorizer=None,
        office_authorization_method: str | None = None,
        office_native_author: NativeWikiBatchAuthor | None = None,
        office_visual_author: OfficeHybridVisualAuthor | None = None):
    """One explicit policy/registry for API and durable worker bootstraps.

    Authors and model policies must be supplied by the server, not an upload
    request. This does not register routes, change feature flags, migrate a DB,
    call a model or execute pending work. Historical semantic-review work may
    still drain through the retained legacy handler; new uploads never opt in.
    """
    if (type(legacy_policy) is not WikiPreservationUploadPolicy
            or legacy_policy.preservation_version not in AUTHORING_ONLY_VERSIONS):
        raise ValueError('New publication composition requires authoring-only policy.')
    role_enabled = pptx_role_author is not None or pptx_role_policy is not None
    pdf_enabled = any(value is not None for value in (pdf_author,pdf_policy,pdf_python_executable))
    visual_enabled = pdf_visual_author is not None or pdf_visual_policy is not None
    if (type(pdf_continuation_enabled) is not bool or
            pdf_continuation_enabled != (factory.migration_catalog in (
                PDF_CONTEXT_RESUME_MIGRATION_CATALOG, PDF_TERMINAL_RESULT_REUSE_MIGRATION_CATALOG))):
        raise ValueError("Continuation composition requires its explicit matching V97 activation.")
    office_catalog = factory.migration_catalog.migrations in (
        PDF_LAYOUT_RETENTION_MIGRATION_CATALOG.migrations,
        PDF_LAYOUT_MATH_FORMAT_MIGRATION_CATALOG.migrations,
        PDF_LAYOUT_SUBJECT_BINDING_MIGRATION_CATALOG.migrations,
        PDF_LAYOUT_NONBLANK_GROUPS_MIGRATION_CATALOG.migrations,
        PDF_LAYOUT_LITERAL_APPENDIX_MIGRATION_CATALOG.migrations,
        PDF_FIGURE_ROI_MIGRATION_CATALOG.migrations,
        PDF_CURVE_DOMAIN_MIGRATION_CATALOG.migrations,
        PDF_NONPLOT_WRITING_MIGRATION_CATALOG.migrations,
        PDF_NONTHINKING_WRITING_MIGRATION_CATALOG.migrations,
        PDF_CURVE_ARRAY_GUIDANCE_MIGRATION_CATALOG.migrations,
        PDF_TEXT_OUTPUT_HEADROOM_MIGRATION_CATALOG.migrations,
        PDF_FINITE_TEXT_WRITING_MIGRATION_CATALOG.migrations,
        PDF_BOUNDED_TEXT_PARTITION_MIGRATION_CATALOG.migrations,
        PDF_CURVE_NAMED_BOUNDS_MIGRATION_CATALOG.migrations,
        PDF_CURVE_PROMPT_ALIGNMENT_MIGRATION_CATALOG.migrations,
        PDF_VISUAL_CONTRACT_ALIGNMENT_MIGRATION_CATALOG.migrations,
        PDF_TEXT_JSON_CONTRACT_ALIGNMENT_MIGRATION_CATALOG.migrations,
        PDF_NATIVE_FRAME_CONTRACT_MIGRATION_CATALOG.migrations,
        PDF_COMPLETE_SOURCE_PACKING_MIGRATION_CATALOG.migrations,
        PDF_NATIVE_CATEGORICAL_AXES_MIGRATION_CATALOG.migrations,
        PDF_NATIVE_TICK_CONDITIONS_MIGRATION_CATALOG.migrations,
        PDF_V10_LITERAL_TABLES_MIGRATION_CATALOG.migrations,
        PDF_V10_CHART_TABLES_MIGRATION_CATALOG.migrations,
        PDF_CHART_LABELS_MIGRATION_CATALOG.migrations,
        PDF_NUMERIC_APPEND_MIGRATION_CATALOG.migrations,
        PDF_ATOMIC_REQUEST_PARTITION_MIGRATION_CATALOG.migrations,
        PDF_CONTEXT_RESUME_MIGRATION_CATALOG.migrations,
        PDF_TERMINAL_RESULT_REUSE_MIGRATION_CATALOG.migrations,
        OFFICE_HYBRID_PUBLICATION_BODY_MIGRATION_CATALOG.migrations,
        OFFICE_PERSONAL_WRITER_PART_MIGRATION_CATALOG.migrations,
        COMPANY_POLICY_PROVENANCE_MIGRATION_CATALOG.migrations,
        PDF_OWNED_EVIDENCE_PROMPT_MIGRATION_CATALOG.migrations,
        PDF_FENCED_RESPONSE_SNAPSHOT_MIGRATION_CATALOG.migrations,
        PDF_SECTION_WRITING_MIGRATION_CATALOG.migrations, PDF_SECTION_JSON_OUTPUT_MIGRATION_CATALOG.migrations, PDF_SECTION_GUARD_DISPATCH_MIGRATION_CATALOG.migrations, PDF_SECTION_SCHEMA_OUTPUT_MIGRATION_CATALOG.migrations, PDF_SECTION_MARKDOWN_OUTPUT_MIGRATION_CATALOG.migrations, PDF_SECTION_MARKDOWN_DELIMITERS_MIGRATION_CATALOG.migrations, PDF_BATCH_MARKDOWN_MIGRATION_CATALOG.migrations, PDF_SOURCE_FAITHFUL_MARKDOWN_MIGRATION_CATALOG.migrations)
    office_enabled = any(value is not None for value in (
        office_policy, office_renderer, office_export_authorizer,
        office_authorization_method, office_native_author, office_visual_author))
    if office_catalog != office_enabled:
        raise ValueError('The Office v58 catalog requires explicit Office routing.')
    if office_enabled and (type(office_policy) is not OfficeHybridUploadPolicy
            or not callable(getattr(office_renderer, 'render_for_actor', None))
            or not callable(office_export_authorizer)
            or type(office_authorization_method) is not str
            or not office_authorization_method.strip()
            or type(office_native_author) is not NativeWikiBatchAuthor
            or type(office_visual_author) is not OfficeHybridVisualAuthor):
        raise ValueError('Office routing requires its exact renderer, authors and export authority.')
    visual_catalogs = (PDF_VISUAL_PUBLICATION_MIGRATION_CATALOG.migrations,
        PDF_LAYOUT_RETENTION_MIGRATION_CATALOG.migrations,
        PDF_LAYOUT_MATH_FORMAT_MIGRATION_CATALOG.migrations,
        PDF_LAYOUT_SUBJECT_BINDING_MIGRATION_CATALOG.migrations,
        PDF_LAYOUT_NONBLANK_GROUPS_MIGRATION_CATALOG.migrations,
        PDF_LAYOUT_LITERAL_APPENDIX_MIGRATION_CATALOG.migrations,
        PDF_FIGURE_ROI_MIGRATION_CATALOG.migrations,
        PDF_CURVE_DOMAIN_MIGRATION_CATALOG.migrations,
        PDF_NONPLOT_WRITING_MIGRATION_CATALOG.migrations,
        PDF_NONTHINKING_WRITING_MIGRATION_CATALOG.migrations,
        PDF_CURVE_ARRAY_GUIDANCE_MIGRATION_CATALOG.migrations,
        PDF_TEXT_OUTPUT_HEADROOM_MIGRATION_CATALOG.migrations,
        PDF_FINITE_TEXT_WRITING_MIGRATION_CATALOG.migrations,
        PDF_BOUNDED_TEXT_PARTITION_MIGRATION_CATALOG.migrations,
        PDF_CURVE_NAMED_BOUNDS_MIGRATION_CATALOG.migrations,
        PDF_CURVE_PROMPT_ALIGNMENT_MIGRATION_CATALOG.migrations,
        PDF_VISUAL_CONTRACT_ALIGNMENT_MIGRATION_CATALOG.migrations,
        PDF_TEXT_JSON_CONTRACT_ALIGNMENT_MIGRATION_CATALOG.migrations,
        PDF_NATIVE_FRAME_CONTRACT_MIGRATION_CATALOG.migrations,
        PDF_COMPLETE_SOURCE_PACKING_MIGRATION_CATALOG.migrations,
        PDF_NATIVE_CATEGORICAL_AXES_MIGRATION_CATALOG.migrations,
        PDF_NATIVE_TICK_CONDITIONS_MIGRATION_CATALOG.migrations,
        PDF_V10_LITERAL_TABLES_MIGRATION_CATALOG.migrations,
        PDF_V10_CHART_TABLES_MIGRATION_CATALOG.migrations,
        PDF_CHART_LABELS_MIGRATION_CATALOG.migrations,
        PDF_NUMERIC_APPEND_MIGRATION_CATALOG.migrations,
        PDF_ATOMIC_REQUEST_PARTITION_MIGRATION_CATALOG.migrations,
        PDF_CONTEXT_RESUME_MIGRATION_CATALOG.migrations,
        PDF_TERMINAL_RESULT_REUSE_MIGRATION_CATALOG.migrations,
        PDF_VISUAL_WRITING_POLICY_MIGRATION_CATALOG.migrations,
        PDF_EVIDENCE_UNITS_MIGRATION_CATALOG.migrations,
        NATIVE_SEMANTIC_TARGETS_MIGRATION_CATALOG.migrations,
        SEMANTIC_RENAME_RESEED_MIGRATION_CATALOG.migrations,
        PROTECTED_EXTRACTION_PROVENANCE_MIGRATION_CATALOG.migrations,
        PROTECTED_PDF_VISUAL_BINDING_MIGRATION_CATALOG.migrations,
        TEMPORAL_ROOT_REBINDING_MIGRATION_CATALOG.migrations,
        OFFICE_HYBRID_PUBLICATION_BODY_MIGRATION_CATALOG.migrations,
        OFFICE_PERSONAL_WRITER_PART_MIGRATION_CATALOG.migrations,
        COMPANY_POLICY_PROVENANCE_MIGRATION_CATALOG.migrations,
        PDF_OWNED_EVIDENCE_PROMPT_MIGRATION_CATALOG.migrations,
        PDF_FENCED_RESPONSE_SNAPSHOT_MIGRATION_CATALOG.migrations,
        PDF_SECTION_WRITING_MIGRATION_CATALOG.migrations, PDF_SECTION_JSON_OUTPUT_MIGRATION_CATALOG.migrations, PDF_SECTION_GUARD_DISPATCH_MIGRATION_CATALOG.migrations, PDF_SECTION_SCHEMA_OUTPUT_MIGRATION_CATALOG.migrations, PDF_SECTION_MARKDOWN_OUTPUT_MIGRATION_CATALOG.migrations, PDF_SECTION_MARKDOWN_DELIMITERS_MIGRATION_CATALOG.migrations, PDF_BATCH_MARKDOWN_MIGRATION_CATALOG.migrations, PDF_SOURCE_FAITHFUL_MARKDOWN_MIGRATION_CATALOG.migrations)
    protected_pdf_visual = (factory.migration_catalog.migrations in (
            PDF_LAYOUT_RETENTION_MIGRATION_CATALOG.migrations,
            PDF_LAYOUT_MATH_FORMAT_MIGRATION_CATALOG.migrations,
            PDF_LAYOUT_SUBJECT_BINDING_MIGRATION_CATALOG.migrations,
            PDF_LAYOUT_NONBLANK_GROUPS_MIGRATION_CATALOG.migrations,
        PDF_LAYOUT_LITERAL_APPENDIX_MIGRATION_CATALOG.migrations,
        PDF_FIGURE_ROI_MIGRATION_CATALOG.migrations,
        PDF_CURVE_DOMAIN_MIGRATION_CATALOG.migrations,
        PDF_NONPLOT_WRITING_MIGRATION_CATALOG.migrations,
        PDF_NONTHINKING_WRITING_MIGRATION_CATALOG.migrations,
        PDF_CURVE_ARRAY_GUIDANCE_MIGRATION_CATALOG.migrations,
        PDF_TEXT_OUTPUT_HEADROOM_MIGRATION_CATALOG.migrations,
        PDF_FINITE_TEXT_WRITING_MIGRATION_CATALOG.migrations,
        PDF_BOUNDED_TEXT_PARTITION_MIGRATION_CATALOG.migrations,
        PDF_CURVE_NAMED_BOUNDS_MIGRATION_CATALOG.migrations,
        PDF_CURVE_PROMPT_ALIGNMENT_MIGRATION_CATALOG.migrations,
        PDF_VISUAL_CONTRACT_ALIGNMENT_MIGRATION_CATALOG.migrations,
        PDF_TEXT_JSON_CONTRACT_ALIGNMENT_MIGRATION_CATALOG.migrations,
        PDF_NATIVE_FRAME_CONTRACT_MIGRATION_CATALOG.migrations,
        PDF_COMPLETE_SOURCE_PACKING_MIGRATION_CATALOG.migrations,
        PDF_NATIVE_CATEGORICAL_AXES_MIGRATION_CATALOG.migrations,
        PDF_NATIVE_TICK_CONDITIONS_MIGRATION_CATALOG.migrations,
        PDF_V10_LITERAL_TABLES_MIGRATION_CATALOG.migrations,
        PDF_V10_CHART_TABLES_MIGRATION_CATALOG.migrations,
        PDF_CHART_LABELS_MIGRATION_CATALOG.migrations,
        PDF_NUMERIC_APPEND_MIGRATION_CATALOG.migrations,
        PDF_ATOMIC_REQUEST_PARTITION_MIGRATION_CATALOG.migrations,
        PDF_CONTEXT_RESUME_MIGRATION_CATALOG.migrations,
        PDF_TERMINAL_RESULT_REUSE_MIGRATION_CATALOG.migrations,
            PROTECTED_PDF_VISUAL_BINDING_MIGRATION_CATALOG.migrations,
            TEMPORAL_ROOT_REBINDING_MIGRATION_CATALOG.migrations,
            OFFICE_HYBRID_PUBLICATION_BODY_MIGRATION_CATALOG.migrations,
            OFFICE_PERSONAL_WRITER_PART_MIGRATION_CATALOG.migrations,
            COMPANY_POLICY_PROVENANCE_MIGRATION_CATALOG.migrations,
            PDF_OWNED_EVIDENCE_PROMPT_MIGRATION_CATALOG.migrations,
            PDF_FENCED_RESPONSE_SNAPSHOT_MIGRATION_CATALOG.migrations,
            PDF_SECTION_WRITING_MIGRATION_CATALOG.migrations, PDF_SECTION_JSON_OUTPUT_MIGRATION_CATALOG.migrations, PDF_SECTION_GUARD_DISPATCH_MIGRATION_CATALOG.migrations, PDF_SECTION_SCHEMA_OUTPUT_MIGRATION_CATALOG.migrations, PDF_SECTION_MARKDOWN_OUTPUT_MIGRATION_CATALOG.migrations, PDF_SECTION_MARKDOWN_DELIMITERS_MIGRATION_CATALOG.migrations, PDF_BATCH_MARKDOWN_MIGRATION_CATALOG.migrations, PDF_SOURCE_FAITHFUL_MARKDOWN_MIGRATION_CATALOG.migrations)
        and protected_pdf_opener is not None)
    visual_catalog = factory.migration_catalog.migrations in visual_catalogs
    pdf_catalog = factory.migration_catalog.migrations in (PDF_PUBLICATION_MIGRATION_CATALOG.migrations,
        *visual_catalogs)
    if visual_catalog and not visual_enabled:
        raise ValueError('The visual PDF catalog requires explicit visual routing, not legacy fallback.')
    if visual_enabled and (not visual_catalog or pdf_visual_author is None
            or type(pdf_visual_policy) is not PdfVisualUploadPolicy or not pdf_enabled):
        raise ValueError('Visual PDF routing requires its exact catalog, author and retained PDF route.')
    if pdf_catalog and not pdf_enabled:
        raise ValueError('The PDF catalog requires explicit PDF routing, not legacy fallback.')
    if pdf_enabled and (not pdf_catalog or pdf_author is None or type(pdf_policy) is not PdfUploadPolicy
            or not isinstance(pdf_python_executable, (str, Path)) or not str(pdf_python_executable).strip()
            or not role_enabled or display_policy is not None):
        raise ValueError('PDF routing requires its exact catalog, parser, author and explicit role route.')
    if visual_enabled and (pdf_policy.geometry_limits != pdf_visual_policy.geometry_limits
            or pdf_policy.process_policy != pdf_visual_policy.process_policy):
        raise ValueError('Visual and retained PDF routes require the same configured parser limits.')
    role_catalogs = (PPTX_ROLE_PUBLICATION_MIGRATION_CATALOG.migrations,
                    LEGACY_AUTHORING_RETENTION_MIGRATION_CATALOG.migrations,
                    PDF_PUBLICATION_MIGRATION_CATALOG.migrations,
                    *visual_catalogs)
    if factory.migration_catalog.migrations in role_catalogs and not role_enabled:
        raise ValueError('The role catalog requires explicit role routing, not native fallback.')
    if role_enabled and (pptx_role_author is None or type(pptx_role_policy) is not PptxRoleUploadPolicy
            or factory.migration_catalog.migrations not in role_catalogs
            or display_policy is not None):
        raise ValueError('Role routing requires its exact catalog and one distinct PPT policy.')
    policy_catalog = factory.migration_catalog.migrations in (
        PDF_LAYOUT_RETENTION_MIGRATION_CATALOG.migrations,
        PDF_LAYOUT_MATH_FORMAT_MIGRATION_CATALOG.migrations,
        PDF_LAYOUT_SUBJECT_BINDING_MIGRATION_CATALOG.migrations,
        PDF_LAYOUT_NONBLANK_GROUPS_MIGRATION_CATALOG.migrations,
        PDF_LAYOUT_LITERAL_APPENDIX_MIGRATION_CATALOG.migrations,
        PDF_FIGURE_ROI_MIGRATION_CATALOG.migrations,
        PDF_CURVE_DOMAIN_MIGRATION_CATALOG.migrations,
        PDF_NONPLOT_WRITING_MIGRATION_CATALOG.migrations,
        PDF_NONTHINKING_WRITING_MIGRATION_CATALOG.migrations,
        PDF_CURVE_ARRAY_GUIDANCE_MIGRATION_CATALOG.migrations,
        PDF_TEXT_OUTPUT_HEADROOM_MIGRATION_CATALOG.migrations,
        PDF_FINITE_TEXT_WRITING_MIGRATION_CATALOG.migrations,
        PDF_BOUNDED_TEXT_PARTITION_MIGRATION_CATALOG.migrations,
        PDF_CURVE_NAMED_BOUNDS_MIGRATION_CATALOG.migrations,
        PDF_CURVE_PROMPT_ALIGNMENT_MIGRATION_CATALOG.migrations,
        PDF_VISUAL_CONTRACT_ALIGNMENT_MIGRATION_CATALOG.migrations,
        PDF_TEXT_JSON_CONTRACT_ALIGNMENT_MIGRATION_CATALOG.migrations,
        PDF_NATIVE_FRAME_CONTRACT_MIGRATION_CATALOG.migrations,
        PDF_COMPLETE_SOURCE_PACKING_MIGRATION_CATALOG.migrations,
        PDF_NATIVE_CATEGORICAL_AXES_MIGRATION_CATALOG.migrations,
        PDF_NATIVE_TICK_CONDITIONS_MIGRATION_CATALOG.migrations,
        PDF_V10_LITERAL_TABLES_MIGRATION_CATALOG.migrations,
        PDF_V10_CHART_TABLES_MIGRATION_CATALOG.migrations,
        PDF_CHART_LABELS_MIGRATION_CATALOG.migrations,
        PDF_NUMERIC_APPEND_MIGRATION_CATALOG.migrations,
        PDF_ATOMIC_REQUEST_PARTITION_MIGRATION_CATALOG.migrations,
        PDF_CONTEXT_RESUME_MIGRATION_CATALOG.migrations,
        PDF_TERMINAL_RESULT_REUSE_MIGRATION_CATALOG.migrations,
        COMPANY_POLICY_PROVENANCE_MIGRATION_CATALOG.migrations,
        PDF_OWNED_EVIDENCE_PROMPT_MIGRATION_CATALOG.migrations,
        PDF_FENCED_RESPONSE_SNAPSHOT_MIGRATION_CATALOG.migrations,
        PDF_SECTION_WRITING_MIGRATION_CATALOG.migrations,
        PDF_SECTION_JSON_OUTPUT_MIGRATION_CATALOG.migrations,
        PDF_SECTION_GUARD_DISPATCH_MIGRATION_CATALOG.migrations,
        PDF_SECTION_SCHEMA_OUTPUT_MIGRATION_CATALOG.migrations, PDF_SECTION_MARKDOWN_OUTPUT_MIGRATION_CATALOG.migrations, PDF_SECTION_MARKDOWN_DELIMITERS_MIGRATION_CATALOG.migrations, PDF_BATCH_MARKDOWN_MIGRATION_CATALOG.migrations, PDF_SOURCE_FAITHFUL_MARKDOWN_MIGRATION_CATALOG.migrations,
    )
    if ((policy_catalog and (policy_protection_classifier is None
            or protected_publication_authorizer is None))
            or (policy_protection_classifier is not None
                and (not policy_catalog
                    or not callable(getattr(policy_protection_classifier, 'classify_for_actor', None))
                    or protected_publication_authorizer is None))):
        raise ValueError('Policy-protected routing requires one v60 classifier and current authority.')
    publish_authority = dict(
        protected_publication_authorizer=protected_publication_authorizer,
        require_protected_publication_authorization=require_protected_publication_authorization,
    )
    if policy_catalog:
        publish_authority.update(
            policy_protection_classifier=policy_protection_classifier,
            policy_source_loader=CanonicalPublicationSourceLoader(factory, stager),
        )
    office_registration = (OfficeHybridUploadRegistration(factory, policy=office_policy)
        if office_enabled else None)
    office_handler = (OfficeHybridPreviewExtractionHandler(factory, stager,
        scope_resolver=scope_resolver, renderer=office_renderer,
        python_executable=pdf_python_executable,
        export_authorizer=office_export_authorizer,
        authorization_method=office_authorization_method,
        policy=office_policy,
        policy_protection_classifier=policy_protection_classifier) if office_enabled else None)
    office_source = (CanonicalOfficeHybridAuthoringSourceService(factory, stager,
        archive=office_handler.archives, policy=office_policy)
        if office_enabled else None)
    office_publication_source = (OfficeHybridPublicationSourceService(factory,
        source=office_source) if office_enabled else None)
    office_starter = (OfficeHybridWikiWriteService(factory, source=office_source)
        if office_enabled else None)
    office_dispatch = (OfficeHybridWikiDispatchHandler(factory, starter=office_starter)
        if office_enabled else None)
    office_writer = (OfficeHybridWikiWriteHandler(factory, source=office_source,
        native_author=office_native_author, visual_author=office_visual_author)
        if office_enabled else None)
    publisher = (OfficeHybridPublicationService(factory,scope_resolver,stager,
        python_executable=pdf_python_executable,office_source=office_publication_source,
        geometry_limits=pdf_visual_policy.geometry_limits,
        process_policy=pdf_visual_policy.process_policy,
        render_policy=pdf_visual_policy.render_policy,
        asset_policy=pdf_visual_policy.retained_asset_policy,
        chart_ocr_language=pdf_visual_policy.chart_ocr_language, chart_limits=pdf_visual_policy.chart_limits,
        protected_pdf_opener=protected_pdf_opener if protected_pdf_visual else None,
        **publish_authority) if office_enabled else
        PdfVisualPublicationService(factory,scope_resolver,stager,
        python_executable=pdf_python_executable,geometry_limits=pdf_visual_policy.geometry_limits,
        process_policy=pdf_visual_policy.process_policy,render_policy=pdf_visual_policy.render_policy,
        asset_policy=pdf_visual_policy.retained_asset_policy,
        chart_ocr_language=pdf_visual_policy.chart_ocr_language, chart_limits=pdf_visual_policy.chart_limits,
        protected_pdf_opener=protected_pdf_opener if protected_pdf_visual else None,
        **publish_authority) if visual_enabled else
        PdfPublicationService(factory,scope_resolver,stager,
        python_executable=pdf_python_executable,geometry_limits=pdf_policy.geometry_limits,
        process_policy=pdf_policy.process_policy,**publish_authority) if pdf_enabled else
        (PptxRolePublicationService if role_enabled else CrossKindPublicationService)(
            factory,scope_resolver,stager,**publish_authority))
    native=NativeAuthoringWorkflow(factory,scope_resolver,stager,author=native_author,policy=native_policy)
    display = None
    if display_policy is not None:
        expected = NATIVE_DISPLAY_PUBLICATION_MIGRATION_CATALOG.migrations
        if (type(display_policy) is not NativeUploadPolicy
                or factory.migration_catalog.migrations != expected
                or native.policy.source_preparation_version == DISPLAY_SOURCE_POLICY
                or display_policy.source_preparation_version != DISPLAY_SOURCE_POLICY):
            raise ValueError('Display routing requires its verified catalog and distinct format policies.')
        # This instance only registers new uploads. The single native coordinator
        # below restores both immutable families; no duplicate kernel/handlers.
        display=NativeAuthoringWorkflow(factory,scope_resolver,stager,author=native_author,policy=display_policy)
    legacy=WikiPreservationWorkflow(factory,author=legacy_author,policy=legacy_policy,
        protected_authoring_authorizer=protected_authoring_authorizer,
        require_protected_authoring_authorization=require_protected_authoring_authorization)
    role = (PptxRoleAuthoringWorkflow(factory,scope_resolver,stager,author=pptx_role_author,
        policy=pptx_role_policy) if role_enabled else None)
    pdf = (PdfAuthoringWorkflow(factory,scope_resolver,stager,author=pdf_author,
        policy=pdf_policy,python_executable=pdf_python_executable) if pdf_enabled else None)
    pdf_visual = (PdfVisualAuthoringWorkflow(factory,scope_resolver,stager,author=pdf_visual_author,
        policy=pdf_visual_policy,python_executable=pdf_python_executable,
        protected_pdf_opener=protected_pdf_opener if protected_pdf_visual else None,
        protected_authoring_authorizer=protected_authoring_authorizer,
        require_protected_authoring_authorization=require_protected_authoring_authorization) if visual_enabled else None)
    continuation = None
    if pdf_continuation_enabled:
        from .pdf_visual_continuation import PdfContinuationWorkflow
        from .pdf_visual_continuation_publication import (
            PdfContinuationPublicationService, PdfContinuationOfficeHybridPublicationService,
        )
        if pdf_visual is None or not pdf_visual_policy.forward_partition:
            raise ValueError('Continuation routing requires its original range-partition workflow.')
        continuation = PdfContinuationWorkflow(pdf_visual)
        successor = (PdfContinuationOfficeHybridPublicationService if office_enabled
            else PdfContinuationPublicationService)
        publisher = successor(factory,scope_resolver,stager,continuation=continuation,
            python_executable=pdf_python_executable,
            geometry_limits=pdf_visual_policy.geometry_limits,
            process_policy=pdf_visual_policy.process_policy,
            render_policy=pdf_visual_policy.render_policy,
            asset_policy=pdf_visual_policy.retained_asset_policy,
            chart_ocr_language=pdf_visual_policy.chart_ocr_language,
            chart_limits=pdf_visual_policy.chart_limits,
            protected_pdf_opener=protected_pdf_opener if protected_pdf_visual else None,
            **({'office_source':office_publication_source} if office_enabled else {}),
            **publish_authority)
    selector=(OfficeHybridPublicationSelector if office_enabled else
        PdfVisualPublicationSelector if visual_enabled else PdfPublicationSelector if pdf_enabled else
        PptxRolePublicationSelector if role_enabled else CrossKindPublicationSelector)(factory)
    if continuation is not None:
        from .pdf_visual_continuation_publication import PdfContinuationPublicationSelector
        selector = PdfContinuationPublicationSelector(factory,selector)
    previews=_FormatRoutedPreviewService(factory,scope_resolver,stager,legacy=legacy,native=native,
        selector=selector,display=display,pptx_role=role,pdf=pdf,pdf_visual=pdf_visual,
        protected_pdf_visual=protected_pdf_visual,
        office_registration=office_registration,
        policy_protection_classifier=policy_protection_classifier)
    extractor=CanonicalRichPublicationExtractor(source_only=True,bounded_table_labels=True,
        protected_pdf_opener=protected_pdf_opener,
        protected_source_opener=protected_source_opener,
        policy_protection_classifier=policy_protection_classifier)
    extraction_v1=PublicationPreviewExtractionHandler(factory,CanonicalPublicationSourceLoader(factory,stager),
        _ProtectedPdfVisualExtractor(extractor) if visual_enabled else extractor,
        asset_stager=stager)
    extraction_v2=NativePublicationPreviewExtractionHandler(factory,stager,scope_resolver=scope_resolver)
    kernel=DurableWorkKernel(factory,HandlerRegistry((extraction_v1.registered_handler(),
        extraction_v2.registered_handler(),*legacy.registered_handlers(),*native.registered_handlers(),
        *((office_handler.registered_handler(),) if office_handler else ()),
        *((office_dispatch.registered_handler(),) if office_dispatch else ()),
        *((office_writer.registered_handler(),) if office_writer else ()),
        *(role.registered_handlers() if role else ()),
        *(pdf.registered_handlers() if pdf else ()),
        *(pdf_visual.registered_handlers() if pdf_visual else ()),
        *(continuation.registered_handlers() if continuation else ()),
        *additional_handlers)),scope_resolver=scope_resolver,
        preparation_read_binding=preparation_read_binding)
    legacy.bind_kernel(kernel)
    native.bind_kernel(kernel)
    if role is not None:
        role.bind_kernel(kernel)
    if pdf is not None:
        pdf.bind_kernel(kernel)
    if pdf_visual is not None:
        pdf_visual.bind_kernel(kernel)
    if continuation is not None:
        continuation.bind_kernel(kernel)
    if office_writer is not None:
        office_writer.bind_kernel(kernel)
    if office_dispatch is not None:
        office_dispatch.bind_kernel(kernel)
    if continuation is not None:
        from .pdf_visual_continuation_publication import PdfContinuationPublicationApiService
        api = PdfContinuationPublicationApiService(previews,publisher,continuation=continuation)
    else:
        api = PublicationApiService(previews,publisher)
    return NativePublicationComposition(api,kernel,
        office_starter,continuation)




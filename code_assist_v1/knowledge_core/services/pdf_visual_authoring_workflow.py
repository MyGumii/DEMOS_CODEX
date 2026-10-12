"""Visual PDF upload -> durable jobs -> complete retained Wiki snapshot.

Separate PDF identities/tables preserve existing native/display/PPTX workers.
The publication successor owns item creation; this module never publishes.
Model/CAS/parsing remain outside writer transactions. Current authority and
exact claims are revalidated before every persistent effect.
"""
from dataclasses import asdict, dataclass, field
from contextlib import closing, contextmanager, nullcontext
import sys
import threading
from pathlib import PurePath
import json
import math
import re
import time
from uuid import UUID, uuid5

from ..db import ConnectionFactory, db_now_ms, transaction, TransactionMode
from ..preparation_reads import PreparationReadError
from ..migrations.pdf_visual_authoring_workflow_catalog import PDF_VISUAL_AUTHORING_WORKFLOW_MIGRATION_CATALOG
from ..migrations.v044_pdf_visual_authoring_workflow import VERSION, HANDLER_VERSION, PLAN, BATCH, COMPLETE
from ..migrations.v043_pdf_visual_authoring_storage import (
    POLICY, SOURCE_POLICY, SLOT_POLICY, TABLE_POLICY,
)
from ..migrations.v047_pdf_evidence_units import POLICY_V3
from ..migrations.v061_pdf_owned_evidence_prompt import POLICY_V4
from ..migrations.v066_pdf_section_schema_output import POLICY_V7
from ..migrations.v067_pdf_section_markdown_output import POLICY_V8
from ..migrations.v068_pdf_section_markdown_delimiters import POLICY_V9
from ..migrations.v069_pdf_batch_markdown import POLICY_V10
from ..migrations.v070_pdf_source_faithful_markdown import POLICY_V11
from ..migrations.v071_pdf_layout_retention import POLICY_V12
from ..migrations.v072_pdf_layout_math_format import POLICY_V13
from ..migrations.v073_pdf_layout_subject_binding import POLICY_V14
from ..migrations.v074_pdf_layout_nonblank_groups import POLICY_V15
from ..migrations.v075_pdf_layout_literal_appendix import POLICY_V16
from ..migrations.v076_pdf_figure_roi import POLICY_V17
from ..migrations.v077_pdf_curve_domain import POLICY_V18
from ..migrations.v078_pdf_nonplot_writing import POLICY_V19
from ..migrations.v079_pdf_nonthinking_writing import POLICY_V20
from ..migrations.v080_pdf_curve_array_guidance import POLICY_V21
from ..migrations.v081_pdf_text_output_headroom import POLICY_V22
from ..migrations.v082_pdf_finite_text_writing import POLICY_V23
from ..migrations.v083_pdf_bounded_text_partition import POLICY_V24
from ..migrations.v087_pdf_text_json_contract_alignment import POLICY_V28
from ..migrations.v088_pdf_native_frame_contract import POLICY_V29
from ..migrations.v089_pdf_complete_source_packing import POLICY_V30
from ..migrations.v090_pdf_native_categorical_axes import POLICY_V31
from ..migrations.v091_pdf_native_tick_conditions import POLICY_V32
from ..migrations.v092_pdf_v10_literal_tables import POLICY_V33
from ..migrations.v093_pdf_v10_chart_tables import POLICY_V34
from ..migrations.v094_pdf_chart_labels import POLICY_V35
from ..migrations.v095_pdf_numeric_append import POLICY_IDENTITIES, POLICY_V36
from ..migrations.pdf_chart_labels_catalog import PDF_CHART_LABELS_MIGRATION_CATALOG
from ..migrations.pdf_numeric_append_catalog import PDF_NUMERIC_APPEND_MIGRATION_CATALOG
from ..migrations.pdf_v10_chart_tables_catalog import PDF_V10_CHART_TABLES_MIGRATION_CATALOG
from ..migrations.pdf_v10_literal_tables_catalog import PDF_V10_LITERAL_TABLES_MIGRATION_CATALOG
from ..migrations.v086_pdf_visual_contract_alignment import POLICY_V27
from ..migrations.v085_pdf_curve_prompt_alignment import POLICY_V26
from ..migrations.v084_pdf_curve_named_bounds import POLICY_V25
from ..migrations.pdf_visual_writing_policy_catalog import PDF_VISUAL_WRITING_POLICY_MIGRATION_CATALOG
from ..migrations.pdf_evidence_units_catalog import PDF_EVIDENCE_UNITS_MIGRATION_CATALOG
from ..migrations.pdf_owned_evidence_prompt_catalog import PDF_OWNED_EVIDENCE_PROMPT_MIGRATION_CATALOG
from ..migrations.pdf_section_writing_catalog import PDF_SECTION_WRITING_MIGRATION_CATALOG
from ..migrations.v063_pdf_section_writing import POLICY_V5
from ..migrations.v064_pdf_section_json_output import POLICY_V6
from ..migrations.pdf_section_json_output_catalog import PDF_SECTION_JSON_OUTPUT_MIGRATION_CATALOG
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
from ..migrations.protected_pdf_visual_binding_catalog import PROTECTED_PDF_VISUAL_BINDING_MIGRATION_CATALOG
from ..migrations.temporal_root_rebinding_catalog import TEMPORAL_ROOT_REBINDING_MIGRATION_CATALOG
from ..migrations.office_hybrid_publication_body_catalog import OFFICE_HYBRID_PUBLICATION_BODY_MIGRATION_CATALOG
from ..migrations.office_personal_writer_part_catalog import OFFICE_PERSONAL_WRITER_PART_MIGRATION_CATALOG
from ..pdf_visual_authoring import (
    PdfVisualAuthor, PdfVisualStopped, PdfVisualProviderError, PDF_VISUAL_TRANSIENT_FAILURES,
    PDF_VISUAL_INVALID_PROVIDER_FAILURES, prompt_for_policy,
)
from ...document_pdf_geometry import PdfGeometryLimits
from ...document_pdf_process import PdfProcessPolicy
from ...codex_response import CodexResponseError
from ...llm_response import ModelResponseError
from ..repositories.audit import AuditEventDraft, AuditRepository
from ..repositories.jobs import ClaimFence, JobRepository, JobEnqueueDraft, JobClaimLostError, JobCancellationRequestedError
from ..repositories.pdf_visual_authoring import (
    PdfVisualAuthoringRepository, PreparedVisualRun, PreparedVisualOutcome, PreparedVisualSnapshot,
    prepare_visual_run, prepare_visual_outcome, prepare_visual_snapshot,
    PdfVisualStorageConflict,
)
from ..repositories.outbox import OutboxEventDraft, OutboxRepository
from ..repositories.users import UserRepository
from ..repositories.wiki_preservation import PreservationStorageConflict
from ..scope import Capability
from ..wiki_authoring import AuthoringModelPolicy, AuthoringWorkPolicy, AuthoringError, AuthoringOutputError, AuthoringIncompleteError, AuthoringWorkLimitError, _json, _hash
from ..pdf_batch_markdown import UnboundMarkdownImageOutputError
from ..wiki_preservation import PreservationExecutionPolicy
from ..worker import (
    JobAuthorizationRejectedError, JobExpectedStateConflictError, JobPreparationDeadlineError,
    PermanentJobExecutionError,
    PreparedJobEffect, RegisteredJobHandler, RetryableJobExecutionError,
)
from .pdf_visual_authoring_source import CanonicalPdfVisualSourceService, PdfRetainedAssetPolicy
from ..repositories.pdf_visual_archive import PdfVisualArchiveRepository, PreparedPdfVisualArchive
from ..pdf_visual_source import PdfPageRenderPolicy, renderer_identity
from ..pdf_chart_preparation import PdfChartPreparationLimits, VERSION as CHART_PREPARATION_VERSION, LITERAL_CONTEXT_CLAUSE_VERSION, LITERAL_CONTEXT_BARE_VERSION, LITERAL_CONTEXT_RELATION_VERSION, LITERAL_CONTEXT_EXPLICIT_TABLE_VERSION, LITERAL_CONTEXT_BOUNDED_OWNED_VERSION, LITERAL_CONTEXT_ADMITTED_OWNED_VERSION
from ..pdf_native_chart_input import _LANGUAGE as CHART_OCR_LANGUAGE
from .pdf_visual_authoring_restore import _restore_pdf_visual_run
from .publication import PublicationPreviewService, ProtectedPublicationEvidence

_NS = UUID("2bcc69b7-ade1-4199-ac5e-9b6b9266ea41")
_ACTIVE = ("queued", "retry", "claimed", "running")
_GUARD_ERRORS = (JobAuthorizationRejectedError, JobExpectedStateConflictError,
    JobClaimLostError, JobCancellationRequestedError, PreparationReadError)
# Only stable adapter codes may enter prospective audit details. Provider error
# prose, response bodies and arbitrary metadata strings are never diagnostics.
_PROVIDER_FAILURE_CODES = frozenset(PDF_VISUAL_TRANSIENT_FAILURES) | PDF_VISUAL_INVALID_PROVIDER_FAILURES


class _ParserRightsReadScope:
    """One restore invocation: typed, owner/factory/job/thread bound RO leases.

    Each borrow executes existing factory revalidation and the original two
    autocommit rights reads.  No authority result, transaction or callable is
    retained.  The enclosing existing lifecycle owns physical close.
    """
    __slots__ = ("_owner", "_factory", "_job", "_binding", "_thread",
        "_context", "_lifecycle", "_active", "_entered")

    def __init__(self, owner, job):
        _require(type(owner) is PdfVisualAuthoringWorkflow
            and type(owner._factory) is ConnectionFactory,
            "Invalid ordinary PDF parser rights owner.")
        self._owner, self._factory, self._job = owner, owner._factory, job
        self._binding = self._job_binding(job)
        self._thread = threading.current_thread()
        self._context = self._lifecycle = None
        self._active = self._entered = False

    @staticmethod
    def _job_binding(job):
        return (job.id, job.claim_owner, job.attempt_count, job.actor_user_id,
            job.input_ref["preview_id"], job.input_ref.get("run_id"))

    def _check(self, owner, *, actor_user_id, preview_id):
        _require(self._active and owner is self._owner
            and owner._factory is self._factory
            and threading.current_thread() is self._thread
            and self._job_binding(self._job) == self._binding
            and (actor_user_id, preview_id) == self._binding[3:5],
            "PDF parser rights read scope changed or closed.")

    def bind(self, owner, job):
        self._check(owner, actor_user_id=job.actor_user_id,
            preview_id=job.input_ref["preview_id"])
        _require(job is self._job and self._job_binding(job) == self._binding,
            "PDF parser rights read scope belongs to another claim.")

    def __enter__(self):
        _require(not self._entered and threading.current_thread() is self._thread
            and self._owner._factory is self._factory,
            "PDF parser rights read scope cannot be re-entered.")
        self._entered = True
        self._context = self._factory._read_only_lifecycle()
        self._lifecycle = self._context.__enter__()
        self._active = True
        return self

    def __exit__(self, *error):
        _require(threading.current_thread() is self._thread,
            "PDF parser rights read scope cannot transfer between threads.")
        self._active = False
        return self._context.__exit__(*error)

    @contextmanager
    def borrow(self, owner, *, actor_user_id, preview_id):
        self._check(owner, actor_user_id=actor_user_id, preview_id=preview_id)
        with self._lifecycle.borrow() as connection:
            self._factory._check_owned_read_only(connection)
            _require(not connection.in_transaction,
                "PDF parser rights read must remain an autocommit phase.")
            yield connection


class _ParserCancellation:
    """Event-compatible fresh claim/authority checks on the parser-owning thread.

    A failed check asks the process runner to kill/reap its child first. The
    original guard error is then propagated to the canonical job kernel.
    The short throttle caches no successful authorization for committing data.
    """
    def __init__(self, check, *, poll_interval_seconds=0.25):
        self.check, self.error, self.next_check = check, None, 0.0
        self.poll_interval_seconds = poll_interval_seconds

    def is_set(self):
        if self.error is not None:
            return True
        if time.monotonic() >= self.next_check:
            try:
                self.check()
            except Exception as exc:
                self.error = exc
                return True
            self.next_check = time.monotonic() + self.poll_interval_seconds
        return False


def _require(value, message="PDF workflow identity or state changed."):
    if not value:
        raise JobExpectedStateConflictError(message)


def _figure_roi_identity_matches(value, layout, policy):
    """V17/V18 bounded metadata gate; not pixel/caption/semantic authority."""
    from ..pdf_figure_roi_source import VERSION as roi_version, PdfFigureRoiLimits
    fields={'version','source_raw_sha256','layout_catalog_sha256','dpi','limits','rois'}
    image_fields={'page','unit_id','unit_sha256','bbox','page_pixel_bbox','width','height',
        'source_id','descriptor_sha256','pixel_sha256','encoded_sha256','encoded_bytes'}
    try:
        if (type(value) is not dict or set(value)!=fields or value['version']!=roi_version
                or value['source_raw_sha256']!=layout['source']['geometry']['raw_sha256']
                or value['layout_catalog_sha256']!=layout['catalog_sha256']
                or type(value['dpi']) is not int or value['dpi']!=288
                or type(value['limits']) is not dict
                or _json(value['limits'])!=_json(asdict(PdfFigureRoiLimits()))
                or type(value['rois']) is not list or len(value['rois'])>len(layout['crops'])):
            return False
        unit_ids={crop['source_id'] for crop in layout['crops']}
        seen_units,seen_images=set(),set()
        total_bytes=total_pixels=0
        for image in value['rois']:
            if (type(image) is not dict or set(image)!=image_fields
                    or type(image['page']) is not int or not 1<=image['page']<=len(layout['source']['pages'])
                    or type(image['unit_id']) is not str or image['unit_id'] not in unit_ids
                    or image['unit_id'] in seen_units
                    or type(image['source_id']) is not str
                    or re.fullmatch(r'PROI_[0-9a-f]{64}',image['source_id']) is None
                    or image['source_id'] in seen_images
                    or any(type(image[name]) is not str or re.fullmatch(r'[0-9a-f]{64}',image[name]) is None
                        for name in ('unit_sha256','descriptor_sha256','pixel_sha256','encoded_sha256'))
                    or type(image['width']) is not int or type(image['height']) is not int
                    or min(image['width'],image['height'])<1
                    or image['width']*image['height']>value['limits']['image_pixels']
                    or type(image['encoded_bytes']) is not int
                    or not 1<=image['encoded_bytes']<=value['limits']['image_bytes']):
                return False
            box=image['bbox']; pixels=image['page_pixel_bbox']
            if (type(box) is not list or len(box)!=4
                    or any(type(n) not in (int,float) or not math.isfinite(n) or not 0<=n<=1_000_000 for n in box)
                    or not box[0]<box[2] or not box[1]<box[3]
                    or type(pixels) is not list or len(pixels)!=4
                    or any(type(n) is not int or not 0<=n<=2_147_483_647 for n in pixels)
                    or pixels[2]-pixels[0]!=image['width'] or pixels[3]-pixels[1]!=image['height']
                    or pixels!=[math.floor(box[0]*4),math.floor(box[1]*4),math.ceil(box[2]*4),math.ceil(box[3]*4)]):
                return False
            seen_units.add(image['unit_id']);seen_images.add(image['source_id'])
            total_bytes+=image['encoded_bytes'];total_pixels+=image['width']*image['height']
        return (total_bytes<=policy.render_policy.maximum_total_bytes
            and total_pixels<=policy.render_policy.maximum_total_pixels)
    except (ValueError,TypeError,KeyError,UnicodeError,RecursionError,OverflowError):
        return False


def _require_catalog(factory):
    expected = PDF_VISUAL_AUTHORING_WORKFLOW_MIGRATION_CATALOG.migrations
    if factory.migration_catalog.migrations[:len(expected)] != expected:
        raise ValueError("PDF workflow requires its verified successor catalog.")


@dataclass(frozen=True, slots=True)
class PdfVisualUploadPolicy:
    chunk_bytes: int = 8192
    model: AuthoringModelPolicy = field(default_factory=lambda: AuthoringModelPolicy(max_attempts=1,request_budget_bytes=1_048_576))
    work: AuthoringWorkPolicy = field(default_factory=AuthoringWorkPolicy)
    execution: PreservationExecutionPolicy = field(default_factory=PreservationExecutionPolicy)
    geometry_limits: PdfGeometryLimits = field(default_factory=PdfGeometryLimits)
    process_policy: PdfProcessPolicy = field(default_factory=PdfProcessPolicy)
    render_policy: PdfPageRenderPolicy = field(default_factory=PdfPageRenderPolicy)
    retained_asset_policy: PdfRetainedAssetPolicy = field(default_factory=PdfRetainedAssetPolicy)
    policy_version: str = field(default=POLICY, kw_only=True)
    chart_ocr_language: str | None = field(default=None, kw_only=True)
    chart_limits: PdfChartPreparationLimits = field(default_factory=PdfChartPreparationLimits, kw_only=True)
    forward_partition: bool = field(default=False, kw_only=True)

    def __post_init__(self):
        if (type(self.chunk_bytes) is not int or not 4 <= self.chunk_bytes <= 8192
                or type(self.model) is not AuthoringModelPolicy or type(self.work) is not AuthoringWorkPolicy
                or type(self.execution) is not PreservationExecutionPolicy
                or type(self.geometry_limits) is not PdfGeometryLimits or type(self.process_policy) is not PdfProcessPolicy
                or type(self.render_policy) is not PdfPageRenderPolicy or type(self.retained_asset_policy) is not PdfRetainedAssetPolicy
                or type(self.chart_limits) is not PdfChartPreparationLimits
                or type(self.forward_partition) is not bool
                or self.model.max_attempts!=1 or len(self.model.provider_order)!=1
                or type(self.policy_version) is not str
                or self.policy_version not in {version for version, _ in POLICY_IDENTITIES}):
            raise ValueError("Invalid server-owned PDF upload policy.")
        if self.forward_partition and (self.policy_version != POLICY_V36
                or not 72 <= self.render_policy.dpi <= 144
                or self.model.request_budget_bytes != 1_114_112
                or self.work.logical_input_bytes != 67_108_864):
            raise ValueError('Original-range partition requires its explicit configured forward policy.')
        if self.policy_version in (POLICY_V34, POLICY_V35, POLICY_V36):
            if type(self.chart_ocr_language) is not str or CHART_OCR_LANGUAGE.fullmatch(self.chart_ocr_language) is None:
                raise ValueError('Forward chart policy requires an explicit valid OCR language.')
        elif self.chart_ocr_language is not None:
            raise ValueError('Historical PDF policies cannot attach forward chart OCR settings.')
        if (self.policy_version, self.prompt_sha256) not in POLICY_IDENTITIES:
            raise ValueError("PDF writing policy no longer matches its frozen prompt.")
        if self.policy_version in (POLICY_V35, POLICY_V36) and (
                self.model.request_budget_bytes != 1_114_112 or self.model.max_tokens != 4096):
            raise ValueError('Chart-label uploads require their fixed request and output budget.')
        if len(self.encoded().encode()) > 65536:
            raise ValueError("PDF upload policy exceeds its storage budget.")

    @property
    def source_preparation_version(self):
        return SOURCE_POLICY

    @property
    def workflow_version(self):
        return VERSION

    @property
    def handler_version(self):
        return HANDLER_VERSION

    def source_options(self):
        return dict(chunk_bytes=self.chunk_bytes,
            model_policy=self.model, work_policy=self.work, execution_policy=self.execution,
            policy_version=self.policy_version,
            **({'forward_partition':True} if self.forward_partition else {}))

    @property
    def prompt_sha256(self):
        return _hash(prompt_for_policy(self.policy_version))

    def source_configuration(self):
        value = dict(version=SOURCE_POLICY, policy_version=self.policy_version, prompt_sha256=self.prompt_sha256,
            chunk_bytes=self.chunk_bytes, model_policy=asdict(self.model),
            work_policy=asdict(self.work), execution_policy=asdict(self.execution),
            geometry_limits=asdict(self.geometry_limits), process_policy=asdict(self.process_policy),
            render_policy=asdict(self.render_policy),renderer_identity=renderer_identity(),
            retained_asset_policy=asdict(self.retained_asset_policy))
        if self.policy_version in (POLICY_V34, POLICY_V35, POLICY_V36):
            value['chart_preparation'] = dict(version=CHART_PREPARATION_VERSION,
                language=self.chart_ocr_language, limits=asdict(self.chart_limits))
        if self.forward_partition:
            from .pdf_visual_authoring_source import forward_source_descriptor
            from ..pdf_visual_authoring import ATOMIC_REQUEST_PARTITION_V14
            value['forward_source_policy'] = forward_source_descriptor(self.render_policy)
            value['request_partition_policy'] = ATOMIC_REQUEST_PARTITION_V14
        return _json(value)

    def encoded(self):
        return _json(dict(version=VERSION, durable_attempts=2,
            configuration=json.loads(self.source_configuration())))

    @classmethod
    def restore(cls, encoded):
        try:
            config = json.loads(encoded)["configuration"]
            model = dict(config["model_policy"])
            for key in ("provider_order", "model_ids"):
                if model.get(key) is not None:
                    model[key] = tuple(model[key])
            if model.get("wire_models") is not None:
                model["wire_models"] = tuple(tuple(pair) for pair in model["wire_models"])
            chart_options = {}
            if config['policy_version'] in (POLICY_V34, POLICY_V35, POLICY_V36):
                profile = config['chart_preparation']
                if set(profile) != {'version','language','limits'} or profile['version'] != CHART_PREPARATION_VERSION:
                    raise ValueError
                from ..pdf_native_figure_source import PdfNativeFigureLimits
                from ..pdf_native_chart_input import PdfNativeChartInputLimits
                from ..pdf_chart_rotated_y_ocr import PdfRotatedYOcrLimits
                limits = dict(profile['limits'])
                limits.update(native=PdfNativeFigureLimits(**limits['native']),
                    ocr=PdfNativeChartInputLimits(**limits['ocr']), rotated=PdfRotatedYOcrLimits(**limits['rotated']))
                chart_options = dict(chart_ocr_language=profile['language'], chart_limits=PdfChartPreparationLimits(**limits))
            policy = cls(chunk_bytes=config["chunk_bytes"], model=AuthoringModelPolicy(**model),
                work=AuthoringWorkPolicy(**config["work_policy"]), execution=PreservationExecutionPolicy(**config["execution_policy"]),
                geometry_limits=PdfGeometryLimits(**config["geometry_limits"]), process_policy=PdfProcessPolicy(**config["process_policy"]),
                render_policy=PdfPageRenderPolicy(**config["render_policy"]),
                retained_asset_policy=PdfRetainedAssetPolicy(**config["retained_asset_policy"]),
                policy_version=config["policy_version"],
                forward_partition=('request_partition_policy' in config), **chart_options)
            if policy.encoded() != encoded:
                raise ValueError
            return policy
        except (KeyError, TypeError, ValueError) as exc:
            raise JobExpectedStateConflictError("Retained PDF policy is invalid.") from exc


class PdfVisualAuthoringPreviewService(PublicationPreviewService):
    """Explicit PDF producer over the existing legacy-v1 extraction identity."""

    def __init__(self, factory, scope_resolver, stager, *, wiki_upload, **kwargs):
        _require_catalog(factory)
        if type(wiki_upload) is not PdfVisualAuthoringWorkflow or wiki_upload.database_path != factory.database_path:
            raise TypeError("PDF upload requires its same-database workflow.")
        super().__init__(factory, scope_resolver, stager, wiki_upload=wiki_upload, **kwargs)

    def create_upload_preview(self, command):
        if (not isinstance(command.original_filename, str)
                or PurePath(command.original_filename).suffix.lower() != ".pdf"
                or not isinstance(command.media_type, str)
                or command.media_type.split(";", 1)[0].lower().strip()
                    not in {"application/pdf", "application/octet-stream"}):
            raise ValueError("PDF authoring requires a PDF upload.")
        return super().create_upload_preview(command)

    def _upload_request_sha256(self, **fields):
        original = super()._upload_request_sha256(**fields)
        return _hash(_json(dict(legacy_request_sha256=original, version=self._wiki_upload.policy.workflow_version,
            handler_version=self._upload_handler_version, rule_version=self._upload_rule_version,
            policy_sha256=_hash(self._wiki_upload.policy.encoded()))))


class PdfVisualAuthoringWorkflow:
    def __init__(self, factory, scope_resolver, stager, *, python_executable, author, policy=None,
                 protected_pdf_opener=None, protected_authoring_authorizer=None,
                 require_protected_authoring_authorization=False):
        _require_catalog(factory)
        if type(author) is not PdfVisualAuthor:
            raise TypeError("PDF workflow requires its bounded batch author.")
        if protected_authoring_authorizer is not None and not callable(protected_authoring_authorizer):
            raise TypeError("Protected PDF authorizer must be callable.")
        if type(require_protected_authoring_authorization) is not bool:
            raise TypeError("Protected PDF authorization policy must be boolean.")
        self._factory, self.author = factory, author
        self._protected_authoring_authorizer = protected_authoring_authorizer
        self._require_protected_authoring_authorization = require_protected_authoring_authorization
        self.policy = PdfVisualUploadPolicy() if policy is None else policy
        if type(self.policy) is not PdfVisualUploadPolicy:
            raise TypeError("PDF workflow requires its server-owned policy.")
        if self.policy.forward_partition:
            from .pdf_visual_authoring_source import _require_atomic_partition_catalog
            _require_atomic_partition_catalog(factory)
        if self.policy.policy_version != POLICY:
            expected = (PDF_NUMERIC_APPEND_MIGRATION_CATALOG
                if self.policy.policy_version == POLICY_V36 else PDF_CHART_LABELS_MIGRATION_CATALOG
                if self.policy.policy_version == POLICY_V35 else PDF_V10_CHART_TABLES_MIGRATION_CATALOG
                if self.policy.policy_version == POLICY_V34 else PDF_V10_LITERAL_TABLES_MIGRATION_CATALOG
                if self.policy.policy_version == POLICY_V33 else PDF_NATIVE_TICK_CONDITIONS_MIGRATION_CATALOG
                if self.policy.policy_version == POLICY_V32 else
                PDF_NATIVE_CATEGORICAL_AXES_MIGRATION_CATALOG
                if self.policy.policy_version == POLICY_V31 else
                PDF_COMPLETE_SOURCE_PACKING_MIGRATION_CATALOG
                if self.policy.policy_version == POLICY_V30 else
                PDF_NATIVE_FRAME_CONTRACT_MIGRATION_CATALOG
                if self.policy.policy_version == POLICY_V29 else
                PDF_TEXT_JSON_CONTRACT_ALIGNMENT_MIGRATION_CATALOG
                if self.policy.policy_version == POLICY_V28 else
                PDF_VISUAL_CONTRACT_ALIGNMENT_MIGRATION_CATALOG
                if self.policy.policy_version == POLICY_V27 else
                PDF_CURVE_PROMPT_ALIGNMENT_MIGRATION_CATALOG
                if self.policy.policy_version == POLICY_V26 else
                PDF_CURVE_NAMED_BOUNDS_MIGRATION_CATALOG
                if self.policy.policy_version == POLICY_V25 else
                PDF_BOUNDED_TEXT_PARTITION_MIGRATION_CATALOG
                if self.policy.policy_version == POLICY_V24 else
                PDF_FINITE_TEXT_WRITING_MIGRATION_CATALOG
                if self.policy.policy_version == POLICY_V23 else
                PDF_TEXT_OUTPUT_HEADROOM_MIGRATION_CATALOG
                if self.policy.policy_version == POLICY_V22 else
                PDF_CURVE_ARRAY_GUIDANCE_MIGRATION_CATALOG
                if self.policy.policy_version == POLICY_V21 else
                PDF_NONTHINKING_WRITING_MIGRATION_CATALOG
                if self.policy.policy_version == POLICY_V20 else
                PDF_NONPLOT_WRITING_MIGRATION_CATALOG
                if self.policy.policy_version == POLICY_V19 else
                PDF_CURVE_DOMAIN_MIGRATION_CATALOG
                if self.policy.policy_version == POLICY_V18 else
                PDF_FIGURE_ROI_MIGRATION_CATALOG
                if self.policy.policy_version == POLICY_V17 else
                PDF_LAYOUT_LITERAL_APPENDIX_MIGRATION_CATALOG
                if self.policy.policy_version == POLICY_V16 else
                PDF_LAYOUT_NONBLANK_GROUPS_MIGRATION_CATALOG
                if self.policy.policy_version == POLICY_V15 else
                PDF_LAYOUT_SUBJECT_BINDING_MIGRATION_CATALOG
                if self.policy.policy_version == POLICY_V14 else
                PDF_LAYOUT_MATH_FORMAT_MIGRATION_CATALOG
                if self.policy.policy_version == POLICY_V13 else
                PDF_LAYOUT_RETENTION_MIGRATION_CATALOG
                if self.policy.policy_version == POLICY_V12 else
                PDF_SOURCE_FAITHFUL_MARKDOWN_MIGRATION_CATALOG
                if self.policy.policy_version == POLICY_V11 else
                PDF_BATCH_MARKDOWN_MIGRATION_CATALOG
                if self.policy.policy_version == POLICY_V10 else
                PDF_SECTION_MARKDOWN_DELIMITERS_MIGRATION_CATALOG
                if self.policy.policy_version == POLICY_V9 else
                PDF_SECTION_MARKDOWN_OUTPUT_MIGRATION_CATALOG
                if self.policy.policy_version == POLICY_V8 else
                PDF_SECTION_SCHEMA_OUTPUT_MIGRATION_CATALOG
                if self.policy.policy_version == POLICY_V7 else
                PDF_SECTION_JSON_OUTPUT_MIGRATION_CATALOG
                if self.policy.policy_version == POLICY_V6 else
                PDF_SECTION_WRITING_MIGRATION_CATALOG
                if self.policy.policy_version == POLICY_V5 else
                PDF_OWNED_EVIDENCE_PROMPT_MIGRATION_CATALOG
                if self.policy.policy_version == POLICY_V4 else
                PDF_EVIDENCE_UNITS_MIGRATION_CATALOG if self.policy.policy_version == POLICY_V3
                else PDF_VISUAL_WRITING_POLICY_MIGRATION_CATALOG).migrations
            if factory.migration_catalog.migrations[:len(expected)] != expected:
                raise ValueError("PDF writing requires its verified successor catalog.")
        self.source = CanonicalPdfVisualSourceService(factory, scope_resolver, stager,
            python_executable=python_executable, geometry_limits=self.policy.geometry_limits,
            process_policy=self.policy.process_policy,render_policy=self.policy.render_policy,
            asset_policy=self.policy.retained_asset_policy,
            chart_ocr_language=self.policy.chart_ocr_language, chart_limits=self.policy.chart_limits,
            protected_pdf_opener=protected_pdf_opener,
            allow_thin_component_bands=self.policy.policy_version == POLICY_V36,
            allow_literal_definition_contexts=self.policy.policy_version == POLICY_V36,
            allow_native_text_presentation=self.policy.policy_version == POLICY_V36,
            allow_key_information_figures=self.policy.policy_version == POLICY_V36,
            **({'fresh_key_information_version':LITERAL_CONTEXT_ADMITTED_OWNED_VERSION}
               if self.policy.policy_version == POLICY_V36 else {}))
        self.archive=PdfVisualArchiveRepository(self.source,stager)
        self.repo, self.jobs = PdfVisualAuthoringRepository(self.source,self.archive), JobRepository()
        self._kernel = None

    @property
    def database_path(self):
        return self._factory.database_path

    def _check_protected_rights(self, *, actor_user_id, preview_id, _rights_read=None):
        """Recheck current server-owned rights before protected PDF model work."""
        if _rights_read is not None:
            _require(type(_rights_read) is _ParserRightsReadScope, "Invalid PDF parser rights read scope.")
            _rights_read._check(self, actor_user_id=actor_user_id, preview_id=preview_id)
        authorizer = self._protected_authoring_authorizer
        if authorizer is None and not self._require_protected_authoring_authorization:
            return
        phase = (closing(self._factory.open(read_only=True)) if _rights_read is None
            else _rights_read.borrow(self, actor_user_id=actor_user_id, preview_id=preview_id))
        connection = phase.__enter__()
        phase_error = (None, None, None)
        try:
            if connection.execute("""SELECT 1 FROM sqlite_schema WHERE type='table'
                AND name='protected_extraction_provenance'""").fetchone() is None:
                row = connection.execute("""SELECT e.extractor_name
                    FROM publication_preview p
                    JOIN extraction_snapshot e ON e.id=p.extraction_snapshot_id
                    WHERE p.id=? AND p.actor_user_id=?""",
                    (preview_id, actor_user_id)).fetchone()
                if row is None or row["extractor_name"] == "canonical-approved-owner-pdf":
                    raise JobAuthorizationRejectedError("Protected PDF origin catalog is missing.")
                return
            row = connection.execute("""SELECT p.artifact_id,p.extraction_snapshot_id,
                    a.original_filename,a.raw_sha256,e.extractor_name,
                    r.original_sha256 AS protected_sha256,r.derivative_sha256,
                    r.source_kind,r.opening_method
                FROM publication_preview p
                JOIN source_artifact a ON a.id=p.artifact_id
                JOIN extraction_snapshot e ON e.id=p.extraction_snapshot_id
                LEFT JOIN protected_extraction_provenance r
                    ON r.extraction_snapshot_id=e.id AND r.artifact_id=a.id
                WHERE p.id=? AND p.actor_user_id=?""",
                (preview_id, actor_user_id)).fetchone()
        except BaseException as exc:
            phase_error = (type(exc), exc, exc.__traceback__)
            raise
        finally:
            phase.__exit__(*phase_error)
        if row is None:
            raise JobExpectedStateConflictError("Protected PDF authoring source changed.")
        if row["protected_sha256"] is None:
            if row["extractor_name"] == "canonical-approved-owner-pdf":
                raise JobAuthorizationRejectedError("Protected PDF origin record is missing.")
            return
        if (row["extractor_name"] != "canonical-approved-owner-pdf"
                or row["protected_sha256"] != row["raw_sha256"]
                or not row["original_filename"]):
            raise JobAuthorizationRejectedError("Protected PDF origin is inconsistent.")
        if authorizer is None:
            raise JobAuthorizationRejectedError("Current protected PDF permission is required.")
        evidence = ProtectedPublicationEvidence(
            actor_user_id=actor_user_id, preview_id=preview_id,
            artifact_id=row["artifact_id"],
            extraction_snapshot_id=row["extraction_snapshot_id"],
            original_filename=row["original_filename"],
            original_sha256=row["raw_sha256"],
            derivative_sha256=row["derivative_sha256"],
            source_kind=row["source_kind"],
            opening_method=row["opening_method"],
        )
        try:
            allowed = authorizer(evidence) is True
        except Exception:
            allowed = False
        if not allowed:
            raise JobAuthorizationRejectedError("Current protected PDF permission denies authoring.")

    @property
    def kernel(self):
        if self._kernel is None:
            raise RuntimeError("Bind the PDF workflow to its canonical kernel first.")
        return self._kernel

    def bind_kernel(self, kernel):
        if self._kernel is not None or kernel.database_path != self.database_path:
            raise ValueError("PDF workflow requires one same-database kernel.")
        for handler in self.registered_handlers():
            registered = kernel.registry.require(handler.key)
            if registered != handler:
                raise ValueError("PDF handler belongs to another workflow instance.")
        self._kernel = kernel

    def registered_handlers(self):
        shared = dict(actor_kind="user", required_capability=Capability.PUBLISH,
            validate_input=self.validate_input, revalidate_expected_state=self.revalidate_expected_state,
            prepare_terminal=self.prepare_terminal, converge_terminal=self.converge_terminal)
        return (
            RegisteredJobHandler.build(job_type=PLAN, handler_version=HANDLER_VERSION,
                prepare=self.prepare_plan, commit_effect=self.commit_plan,
                reconcile_pending=self.reconcile_pending, **shared),
            RegisteredJobHandler.build(job_type=BATCH, handler_version=HANDLER_VERSION,
                prepare=self.prepare_batch, commit_effect=self.commit_batch, **shared),
            RegisteredJobHandler.build(job_type=COMPLETE, handler_version=HANDLER_VERSION,
                prepare=self.prepare_completion, commit_effect=self.commit_completion, **shared),
        )

    def register_upload(self, connection, *, preview_id, parent):
        _require(connection.in_transaction and connection.database_path == self.database_path)
        current = self.jobs.require(connection, parent.id)
        _require(current == parent and parent.handler_version == "v1"
            and parent.job_type == "publication_preview.extract" and parent.state == "queued")
        # Registration is part of the original upload transaction. A queued
        # preview alone does not authorize attaching a new producer later.
        _require(connection.execute("""SELECT 1 FROM operation_dedup
            WHERE id=? AND status='in_progress' AND operation_type='job:publication_preview.extract'
              AND actor_user_id=? AND operation_id=? AND request_sha256=?""",
            (parent.dedup_id, parent.actor_user_id, parent.operation_id, parent.input_sha256)).fetchone()
            is not None, "PDF registration requires the original pending upload operation.")
        row = connection.execute("SELECT expires_at FROM publication_preview WHERE id=?", (preview_id,)).fetchone()
        _require(row is not None)
        encoded = self.policy.encoded()
        created_at = db_now_ms(connection)
        connection.execute("""INSERT INTO pdf_visual_workflow_request
            (preview_id,parent_job_id,policy_sha256,policy_size_bytes,created_at,deadline_at,
             workflow_version,handler_version,configuration_sha256,source_preparation_version,
             source_slot_version,assembly_version,batch_policy_version,prompt_sha256,table_rendering_version)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (preview_id, parent.id, _hash(encoded), len(encoded.encode()), created_at, row[0],
                VERSION, HANDLER_VERSION, _hash(self.policy.source_configuration()), SOURCE_POLICY,
                SLOT_POLICY, self.policy.policy_version, self.policy.policy_version, self.policy.prompt_sha256, TABLE_POLICY))
        connection.execute("INSERT INTO pdf_visual_workflow_policy_body VALUES(?,?)", (preview_id, encoded))
        self._event(connection, parent, "requested", preview_id, dict(preview_id=preview_id))

    def _request(self, connection, preview_id):
        row = connection.execute("""SELECT q.*,p.actor_user_id,p.artifact_id,b.policy_json
            FROM pdf_visual_workflow_request q JOIN publication_preview p ON p.id=q.preview_id
            LEFT JOIN pdf_visual_workflow_policy_body b ON b.preview_id=q.preview_id WHERE q.preview_id=?""",
            (preview_id,)).fetchone()
        _require(row is not None and type(row["policy_json"]) is str
            and _hash(row["policy_json"]) == row["policy_sha256"]
            and len(row["policy_json"].encode()) == row["policy_size_bytes"])
        policy = PdfVisualUploadPolicy.restore(row["policy_json"])
        version, handler, registration = self._request_identity(connection, preview_id)
        _require((policy.workflow_version, policy.handler_version) == (version, handler))
        if registration is not None:
            _require(registration["configuration_sha256"] == _hash(policy.source_configuration())
                and registration["batch_policy_version"] == policy.policy_version
                and registration["prompt_sha256"] == policy.prompt_sha256)
        return {**dict(row), "workflow_version": version, "handler_version": handler}, policy

    def _source_configuration_matches(self, encoded, policy):
        """Keep the verified v51 source binding in later exact-schema catalogs."""
        expected = policy.source_configuration()
        if policy.policy_version in (POLICY_V33, POLICY_V34, POLICY_V35, POLICY_V36):
            try:
                value = json.loads(encoded)
                if type(value) is not dict or _json(value) != encoded:
                    return False
                layout = value.pop('layout_identity')
                if policy.policy_version in (POLICY_V34, POLICY_V35, POLICY_V36):
                    chart = value.pop('chart_identity')
                    if (type(chart) is not dict or set(chart) != {'version','sha256','bytes','source_geometry_sha256',
                            'source_raw_sha256','layout_catalog_sha256','figure_count','publication_authority'}
                            or chart['version'] != 'pdf-chart-supplement-v1' or chart['publication_authority'] is not False
                            or type(chart['bytes']) is not int or not 1 <= chart['bytes'] <= policy.chart_limits.maximum_supplement_bytes
                            or type(chart['figure_count']) is not int or not 0 <= chart['figure_count'] <= policy.chart_limits.maximum_figures
                            or any(type(chart[key]) is not str or re.fullmatch(r'[0-9a-f]{64}',chart[key]) is None
                                for key in ('sha256','source_geometry_sha256','source_raw_sha256','layout_catalog_sha256'))
                            or chart['source_geometry_sha256'] != layout['source']['geometry']['geometry_sha256']
                            or chart['source_raw_sha256'] != layout['source']['geometry']['raw_sha256']
                            or chart['layout_catalog_sha256'] != layout['catalog_sha256']):
                        return False
                if (type(layout) is not dict or set(layout) != {'version','source','catalog_sha256','limits','crops'}
                        or layout.get('version') != 'pdf-layout-evidence-v1'
                        or type(layout.get('source')) is not dict or type(layout.get('limits')) is not dict
                        or type(layout.get('crops')) is not list
                        or type(layout.get('catalog_sha256')) is not str
                        or re.fullmatch(r'[0-9a-f]{64}',layout['catalog_sha256']) is None):
                    return False
                from ...document_pdf_layout import PdfLayoutLimits
                if _json(asdict(PdfLayoutLimits(**layout['limits']))) != _json(layout['limits']):
                    return False
                if 'forward_source_policy' in value:
                    forward = value.pop('forward_source_policy')
                    if (not policy.forward_partition or policy.policy_version != POLICY_V36 or 'protected_binding' in value
                            or _json(forward) != _json({
                                'version':LITERAL_CONTEXT_ADMITTED_OWNED_VERSION,
                                'configured_render_policy':asdict(policy.render_policy),
                                'configured_request_budget_bytes':1_114_112,
                                'effective_request_budget_bytes':4_194_304,
                                'configured_logical_input_bytes':67_108_864,
                                'effective_logical_input_bytes':134_217_728,
                                'all_pages_retained':True,'grid_fidelity_proven':False})):
                        return False
                    effective = PdfPageRenderPolicy(**value['render_policy'])
                    from dataclasses import replace
                    if (not 72 <= effective.dpi <= policy.render_policy.dpi
                            or asdict(replace(effective,dpi=policy.render_policy.dpi)) != asdict(policy.render_policy)
                            or _json(layout['source']['render_policy']) != _json(value['render_policy'])
                            or type(value['model_policy']['request_budget_bytes']) is not int
                            or value['model_policy']['request_budget_bytes'] != 4_194_304
                            or policy.model.request_budget_bytes != 1_114_112
                            or policy.work.logical_input_bytes != 67_108_864
                            or type(value['work_policy']['logical_input_bytes']) is not int
                            or value['work_policy']['logical_input_bytes'] != 134_217_728):
                        return False
                    # Normalize only the explicit bounded transport/canvas
                    # projection, not provider, output, authority or source.
                    value['render_policy'] = asdict(policy.render_policy)
                    value['model_policy']['request_budget_bytes'] = policy.model.request_budget_bytes
                    value['work_policy']['logical_input_bytes'] = policy.work.logical_input_bytes
                    # New registration preserves the exact configured/effective
                    # descriptor; only its three admitted projections differ.
                    value['forward_source_policy'] = forward
                if 'request_partition_policy' in value:
                    from ..pdf_visual_authoring import ATOMIC_REQUEST_PARTITION_V14
                    if (not policy.forward_partition or policy.policy_version != POLICY_V36 or 'protected_binding' in value
                            or 'forward_source_policy' not in json.loads(encoded)
                            or type(value['request_partition_policy']) is not str
                            or value['request_partition_policy'] != ATOMIC_REQUEST_PARTITION_V14):
                        return False
                if 'protected_binding' in value:
                    if self.source._protected_pdf_opener is None or type(value.pop('protected_binding')) is not dict:
                        return False
                return _json(value) == expected
            except (ValueError,TypeError,KeyError,UnicodeError,RecursionError):
                return False
        if policy.policy_version in (POLICY_V12, POLICY_V13, POLICY_V14, POLICY_V15, POLICY_V16, POLICY_V17, POLICY_V18, POLICY_V19, POLICY_V20,POLICY_V21,POLICY_V22,POLICY_V23,POLICY_V24,POLICY_V25, POLICY_V26, POLICY_V27, POLICY_V28, POLICY_V29, POLICY_V30, POLICY_V31, POLICY_V32):
            # The settings policy precedes file preparation. The actual source
            # adds its independently replayed native-layout identity; never
            # treat that persisted identity as a writer-ready capability.
            try:
                value=json.loads(encoded)
                if type(value) is not dict or _json(value)!=encoded:
                    return False
                layout=value.pop('layout_identity')
                if (type(layout) is not dict or layout.get('version')!='pdf-layout-evidence-v1'
                        or set(layout) not in ({'version','source','catalog_sha256','limits','crops'},
                            {'version','source','catalog_sha256','limits','crops','native_figures'})
                        or type(layout.get('source')) is not dict
                        or type(layout.get('limits')) is not dict
                        or type(layout.get('crops')) is not list
                        or type(layout.get('catalog_sha256')) is not str
                        or re.fullmatch(r'[0-9a-f]{64}',layout['catalog_sha256']) is None):
                    return False
                from ...document_pdf_layout import PdfLayoutLimits
                if _json(asdict(PdfLayoutLimits(**layout['limits'])))!=_json(layout['limits']):
                    return False
                crops=layout['crops']
                if (len(crops)>layout['limits']['units']
                        or any(type(crop) is not dict
                            or set(crop)!={'source_id','sha256','bytes','media_type'}
                            or type(crop['source_id']) is not str or not 1<=len(crop['source_id'])<=160
                            or type(crop['sha256']) is not str
                            or re.fullmatch(r'[0-9a-f]{64}',crop['sha256']) is None
                            or type(crop['bytes']) is not int
                            or not 1<=crop['bytes']<=policy.render_policy.maximum_page_bytes
                            or crop['media_type']!='image/png' for crop in crops)
                        or len({crop['source_id'] for crop in crops})!=len(crops)
                        or sum(crop['bytes'] for crop in crops)>policy.render_policy.maximum_total_bytes):
                    return False
                if 'native_figures' in layout:
                    from ..pdf_native_figure_source import PdfNativeFigureLimits, VERSION as native_version
                    native=layout['native_figures']
                    if (type(native) is not dict
                            or set(native)!={'version','source','catalog_sha256','limits','images'}
                            or native['version']!=native_version
                            or _json(native['source'])!=_json(layout['source'])
                            or type(native['catalog_sha256']) is not str
                            or re.fullmatch(r'[0-9a-f]{64}',native['catalog_sha256']) is None
                            or type(native['limits']) is not dict or type(native['images']) is not list
                            or _json(asdict(PdfNativeFigureLimits(**native['limits'])))!=_json(native['limits'])):
                        return False
                    images=native['images']
                    if (len(images)>native['limits']['objects']
                            or any(type(image) is not dict
                                or set(image)!={'source_id','sha256','bytes','media_type'}
                                or type(image['source_id']) is not str
                                or re.fullmatch(r'PN_[0-9a-f]{64}',image['source_id']) is None
                                or type(image['sha256']) is not str
                                or re.fullmatch(r'[0-9a-f]{64}',image['sha256']) is None
                                or type(image['bytes']) is not int
                                or not 1<=image['bytes']<=native['limits']['image_bytes']
                                or image['media_type']!='image/png' for image in images)
                            or len({image['source_id'] for image in images})!=len(images)
                            or sum(image['bytes'] for image in images)>native['limits']['total_bytes']):
                        return False
                if policy.policy_version in (POLICY_V17,POLICY_V18,POLICY_V19,POLICY_V20,POLICY_V21,POLICY_V22,POLICY_V23,POLICY_V24,POLICY_V25, POLICY_V26, POLICY_V27, POLICY_V28, POLICY_V29, POLICY_V30, POLICY_V31, POLICY_V32):
                    if not _figure_roi_identity_matches(value.pop('figure_roi_identity'),layout,policy):
                        return False
                if 'protected_binding' in value:
                    binding=value.pop('protected_binding')
                    if self.source._protected_pdf_opener is None or type(binding) is not dict:
                        return False
                return _json(value)==expected
            except (ValueError,TypeError,KeyError,UnicodeError,RecursionError):
                return False
        if encoded == expected:
            return True
        if (self._factory.migration_catalog.migrations not in (
                PROTECTED_PDF_VISUAL_BINDING_MIGRATION_CATALOG.migrations,
                TEMPORAL_ROOT_REBINDING_MIGRATION_CATALOG.migrations,
                OFFICE_HYBRID_PUBLICATION_BODY_MIGRATION_CATALOG.migrations,
                OFFICE_PERSONAL_WRITER_PART_MIGRATION_CATALOG.migrations)
                or self.source._protected_pdf_opener is None):
            return False
        try:
            value = json.loads(encoded)
            binding = value.pop("protected_binding")
            return type(binding) is dict and _json(value) == expected
        except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
            return False

    def _request_identity(self, connection, preview_id):
        """Permanent version resolution, including cancellation after body purge."""
        request = connection.execute("SELECT * FROM pdf_visual_workflow_request WHERE preview_id=?",
            (preview_id,)).fetchone()
        _require(request is not None)
        writing = request["batch_policy_version"]
        prompt_sha = request["prompt_sha256"]
        _require((writing, prompt_sha) in POLICY_IDENTITIES and tuple(request[k] for k in (
            "workflow_version", "handler_version", "source_preparation_version", "source_slot_version",
            "assembly_version", "batch_policy_version", "prompt_sha256", "table_rendering_version"))
            == (VERSION, HANDLER_VERSION, SOURCE_POLICY, SLOT_POLICY, writing, writing, prompt_sha, TABLE_POLICY))
        return VERSION, HANDLER_VERSION, request

    def _parent(self, connection, request):
        _require(request["deadline_at"] > db_now_ms(connection)
            and not connection.execute("SELECT 1 FROM pdf_visual_workflow_stop WHERE preview_id=?",
                (request["preview_id"],)).fetchone(), "PDF upload expired or stopped.")
        parent = self.kernel.revalidate_job_authority(connection, request["parent_job_id"])
        source = self.source._snapshot(connection, request["preview_id"], request["actor_user_id"], now_ms=0)
        _require(parent.id == source.preview["durable_job_id"])
        return parent, source.fence_sha256

    @staticmethod
    def _identity(kind, value, policy_sha, version=VERSION):
        effect = f"{version}:{kind}:{_hash(_json((value, policy_sha)))}"
        return str(uuid5(_NS, effect)), effect

    @staticmethod
    def validate_input(value, expected):
        _require(set(value) in ({"preview_id"}, {"preview_id", "run_id", "ordinal", "batch_id"},
            {"preview_id", "run_id"}) and set(expected) == {"schema_version", "version", "policy_sha256",
                "parent_job_id", "source_fence_sha256", "source_plan_sha256", "_authorization_fence"})
        _require(type(expected["schema_version"]) is int and expected["schema_version"] == 1
            and expected["version"] == VERSION)
        for key in ("preview_id", "run_id"):
            if key in value:
                _require(type(value[key]) is str and str(UUID(value[key])) == value[key])
        if "ordinal" in value:
            _require(type(value["ordinal"]) is int and 1 <= value["ordinal"] <= 4096)
            _require(type(value["batch_id"]) is str and re.fullmatch(r"PVB_[0-9a-f]{64}", value["batch_id"]) is not None)
        for key in ("policy_sha256", "source_fence_sha256", "source_plan_sha256"):
            if key == "source_plan_sha256" and "run_id" not in value:
                _require(expected[key] is None)
            else:
                _require(type(expected[key]) is str and re.fullmatch(r"[0-9a-f]{64}", expected[key]) is not None)
        _require(type(expected["parent_job_id"]) is str and str(UUID(expected["parent_job_id"])) == expected["parent_job_id"])

    @staticmethod
    def _expected(request, parent, fence, plan_sha):
        return dict(schema_version=1, version=request["workflow_version"], policy_sha256=request["policy_sha256"],
            parent_job_id=parent.id, source_fence_sha256=fence, source_plan_sha256=plan_sha,
            _authorization_fence=parent.expected_state.get("_authorization_fence"))

    def _enqueue(self, connection, kind, value, request, parent, fence, plan_sha=None):
        job_id, effect = self._identity(kind, value, request["policy_sha256"], request["workflow_version"])
        return self.jobs.enqueue(connection, JobEnqueueDraft(job_id=job_id, dedup_id=None, operation_id=None,
            actor_kind="user", actor_user_id=parent.actor_user_id, space_id=parent.space_id,
            job_type=kind, handler_version=request["handler_version"], effect_key=effect, input_ref=value,
            expected_state=self._expected(request, parent, fence, plan_sha),
            authorization_snapshot=parent.authorization_snapshot, authorization_scope_epoch=parent.authorization_scope_epoch,
            authorization_part_id=parent.authorization_part_id, authorization_capabilities=parent.authorization_capabilities,
            max_attempts=2, cancellable=True, available_at_ms=db_now_ms(connection))).job

    def revalidate_expected_state(self, connection, job):
        try:
            self.validate_input(job.input_ref, job.expected_state)
            request, policy = self._request(connection, job.input_ref["preview_id"])
            parent, fence = self._parent(connection, request)
            value, plan_sha = dict(job.input_ref), None
            if job.job_type == PLAN:
                binding = connection.execute("SELECT job_id FROM pdf_visual_workflow_plan WHERE preview_id=?",
                    (request["preview_id"],)).fetchone()
                _require(set(value) == {"preview_id"} and not connection.execute(
                    "SELECT 1 FROM pdf_visual_workflow_planned WHERE preview_id=?", (request["preview_id"],)).fetchone())
            else:
                run = connection.execute("""SELECT r.*,s.status FROM pdf_visual_authoring_run r
                    JOIN pdf_visual_authoring_state s ON s.run_id=r.id WHERE r.id=?""", (value["run_id"],)).fetchone()
                _require(run is not None and run["status"] == "pending"
                    and run["preview_id"] == request["preview_id"] and run["source_fence_sha256"] == fence)
                metadata = json.loads(run["metadata_json"])
                stored = connection.execute("SELECT configuration_json FROM pdf_visual_authoring_run_body WHERE run_id=?",
                    (run["id"],)).fetchone()
                _require(stored is not None
                    and self._source_configuration_matches(stored[0], policy)
                    and metadata["source_preparation_version"] == policy.source_preparation_version
                    and metadata["configuration_sha256"] == _hash(stored[0])
                    and metadata["policy_version"] == policy.policy_version
                    and metadata["assembly_version"] == policy.policy_version
                    and metadata["prompt_sha256"] == policy.prompt_sha256)
                _require(connection.execute("""SELECT 1 FROM pdf_visual_workflow_planned p
                    JOIN pdf_visual_workflow_plan w ON w.preview_id=p.preview_id JOIN durable_job j ON j.id=w.job_id
                    WHERE p.run_id=? AND j.state='succeeded'""", (run["id"],)).fetchone() is not None)
                plan_sha = run["source_plan_sha256"]
                if job.job_type == BATCH:
                    _require(set(value) == {"preview_id", "run_id", "ordinal", "batch_id"})
                    binding = connection.execute("""SELECT w.job_id,b.batch_id FROM pdf_visual_workflow_batch w
                        JOIN pdf_visual_authoring_batch b ON b.run_id=w.run_id AND b.ordinal=w.ordinal
                        WHERE w.run_id=? AND w.ordinal=?""", (run["id"], value["ordinal"])).fetchone()
                    _require(binding is not None and binding[1] == value["batch_id"])
                else:
                    _require(job.job_type == COMPLETE and set(value) == {"preview_id", "run_id"})
                    binding = connection.execute("SELECT job_id FROM pdf_visual_workflow_completion WHERE run_id=?",
                        (run["id"],)).fetchone()
                    _require(self._completed_batches(connection, run["id"]) == run["batch_count"])
            _require(job.handler_version == request["handler_version"] and job.max_attempts == 2 and binding is not None
                and binding[0] == job.id and job.actor_user_id == parent.actor_user_id and job.space_id == parent.space_id
                and (job.id, job.effect_key) == self._identity(job.job_type, value, request["policy_sha256"], request["workflow_version"])
                and _json(dict(job.expected_state)) == _json(self._expected(request, parent, fence, plan_sha)))
        except (KeyError, TypeError, ValueError, AuthoringError, PreservationStorageConflict, PdfVisualStorageConflict) as exc:
            raise JobExpectedStateConflictError("PDF worker source, policy or identity changed.") from exc

    @staticmethod
    def _fence(job):
        return ClaimFence(job.id, job.claim_owner, job.attempt_count)

    @contextmanager
    def _parser_guard(self, job, *, _preflight=None, _poll_interval_seconds=0.25, _rights_read=None):
        fence = self._fence(job)
        # Only private ordinary preparation passes its invocation-owned
        # callback. Every call still performs the exact fresh guard transaction;
        # protected/historical/default callers retain independent ordinary opens.
        _require(_preflight is None or callable(_preflight), "Invalid PDF preparation callback.")
        if _rights_read is not None:
            _require(type(_rights_read) is _ParserRightsReadScope and _preflight is not None,
                "Invalid PDF parser rights read scope.")
            _rights_read.bind(self, job)
        def preflight():
            if _preflight is None:
                return self.kernel.preflight_preparation(fence)
            return _preflight()
        preflight()
        self._check_protected_rights(actor_user_id=job.actor_user_id,
            preview_id=job.input_ref["preview_id"], _rights_read=_rights_read)
        policy = self.source._process_policy
        self.kernel.renew_claim(fence,
            lease_ms=int((2*(policy.timeout_seconds + policy.cleanup_seconds) + 30) * 1000))
        def check():
            preflight()
            self._check_protected_rights(actor_user_id=job.actor_user_id,
                preview_id=job.input_ref["preview_id"], _rights_read=_rights_read)
        cancellation = _ParserCancellation(check, poll_interval_seconds=_poll_interval_seconds)
        try:
            yield cancellation
        except BaseException as exc:
            if cancellation.error is not None:
                raise cancellation.error from exc
            raise
        if cancellation.error is not None:
            raise cancellation.error
        preflight()
        self._check_protected_rights(actor_user_id=job.actor_user_id,
            preview_id=job.input_ref["preview_id"], _rights_read=_rights_read)

    def _restore(self, job, *, _preflight=None):
        connection = self._factory.open(read_only=True)
        try:
            with transaction(connection, TransactionMode.DEFERRED):
                _, policy = self._request(connection, job.input_ref["preview_id"])
                _require(job.handler_version == policy.handler_version
                    and job.expected_state["version"] == policy.workflow_version)
                row=connection.execute("""SELECT r.*,b.configuration_json FROM pdf_visual_authoring_run r
                    JOIN pdf_visual_authoring_run_body b ON b.run_id=r.id WHERE r.id=? AND r.preview_id=?""",
                    (job.input_ref["run_id"],job.input_ref["preview_id"])).fetchone()
                _require(row is not None and row["actor_user_id"]==job.actor_user_id
                    and self._source_configuration_matches(row["configuration_json"], policy)
                    and row["max_executions"]==2)
                archive_id,stored_metadata=row["source_archive_id"],row["metadata_json"]
                ordinary_batch = (policy.policy_version in (POLICY_V31, POLICY_V32, POLICY_V33, POLICY_V34, POLICY_V35, POLICY_V36)
                    and "protected_binding" not in json.loads(row["configuration_json"]))
        finally:
            connection.close()
        run = None
        try:
            rights_context = (_ParserRightsReadScope(self, job)
                if ordinary_batch and policy.policy_version == POLICY_V36 and _preflight is not None
                else nullcontext(None))
            with rights_context as rights_read:
                parser_context = (self._parser_guard(job, _preflight=_preflight if ordinary_batch else None,
                        _rights_read=rights_read) if rights_read is not None
                    else self._parser_guard(job, _preflight=_preflight if ordinary_batch else None))
                with parser_context as cancellation:
                    run=_restore_pdf_visual_run(self.source,self.archive,preview_id=job.input_ref["preview_id"],
                        actor_user_id=job.actor_user_id,source_archive_id=archive_id,max_executions=2,
                        cancel_event=cancellation)
            _require(run.run_id == job.input_ref["run_id"] and run.metadata_json==stored_metadata,
                "Restored visual PDF run differs from its worker.")
            return run
        except BaseException:
            if run is not None:
                run.source.close()
            raise

    def prepare_plan(self, job):
        return self.kernel.prepare_with_read_guard(self._fence(job),
            lambda preflight: self._prepare_plan(job, preflight))

    def _prepare_plan(self, job, preflight):
        fence = self._fence(job)
        self.kernel.preflight_preparation(fence)
        self._check_protected_rights(actor_user_id=job.actor_user_id,
            preview_id=job.input_ref["preview_id"])
        connection = self._factory.open(read_only=True)
        try:
            with transaction(connection, TransactionMode.DEFERRED):
                _, policy = self._request(connection, job.input_ref["preview_id"])
                # Classify only after independent admission, on this existing
                # transaction's freshly verified canonical source; no policy
                # label or retained snapshot grants preparation authority.
                ordinary_plan = (policy.policy_version in (POLICY_V31, POLICY_V32, POLICY_V33, POLICY_V34, POLICY_V35, POLICY_V36)
                    and self.source._snapshot(connection, job.input_ref["preview_id"],
                        job.actor_user_id, now_ms=0).source_extension is None)
        finally:
            connection.close()
        source = archived = None
        try:
            _require(asdict(policy.geometry_limits) == asdict(self.source._limits)
                and asdict(policy.process_policy) == asdict(self.source._process_policy)
                and asdict(policy.render_policy)==asdict(self.source._render_policy)
                and asdict(policy.retained_asset_policy)==asdict(self.source._asset_policy),
                "Retained PDF parser policy differs from this server.")
            # Ordinary source preparation is CPU/parser-only between checks.
            # Limit repeated full schema/authority reads to once per two seconds;
            # every source/CAS fence, guard exit and final commit remains fresh.
            # Protected, historical, restore and completion polling is unchanged.
            with self._parser_guard(job, _preflight=preflight if ordinary_plan else None,
                    _poll_interval_seconds=2.0 if ordinary_plan else 0.25) as cancellation:
                source = self.source.prepare(preview_id=job.input_ref["preview_id"], actor_user_id=job.actor_user_id,
                    **policy.source_options(), cancel_event=cancellation)
                archived=self.archive.prepare(source,operation_id=str(uuid5(_NS,"archive:"+source.preview_id)),
                    cancel_event=cancellation)
            run = prepare_visual_run(source,source_archive_id=archived.metadata[0],max_executions=2)
            self.kernel.preflight_preparation(fence)
            def cleanup():
                try:
                    # Cleanup also runs after rollback. A fresh official RO
                    # succeeded-run/archive guard, never this callback itself,
                    # is the only gate for the optional initial V30 plan seed.
                    try: self._seed_committed_plan(job, run, archived)
                    except Exception: pass  # Pure optimization; guarded cold restore remains available.
                finally:
                    try: archived.close()
                    finally: source.close()
            return PreparedJobEffect(result=dict(schema_version=1, run_id=run.run_id, batch_count=len(run.batches)),
                payload=(run,archived), cleanup=cleanup)
        except BaseException as exc:
            try:
                if isinstance(exc, AuthoringWorkLimitError):
                    self._record_work_limit(job)
            finally:
                try:
                    if archived is not None: archived.close()
                finally:
                    if source is not None: source.close()
            raise

    def _seed_committed_plan(self, job, run, archived):
        from ..pdf_visual_authoring import VERSION_V30, VERSION_V31, VERSION_V32, VERSION_V35, VERSION_V36
        from .pdf_visual_plan_reuse import _seed_committed_preparation
        if (run.source.plan.policy_version not in (VERSION_V30, VERSION_V31, VERSION_V32, VERSION_V35, VERSION_V36)
                or "protected_binding" in json.loads(run.source.configuration_json)):
            return False
        expected = dict(schema_version=1, run_id=run.run_id, batch_count=len(run.batches))
        def check_committed():
            connection = self._factory.open(read_only=True)
            try:
                with transaction(connection, TransactionMode.DEFERRED):
                    current = self.jobs.require(connection, job.id)
                    _require(current.state == "succeeded" and not current.cancel_requested
                        and current.actor_user_id == job.actor_user_id
                        and current.attempt_count == job.attempt_count and current.job_type == PLAN
                        and _json(dict(current.input_ref)) == _json(dict(job.input_ref))
                        and _json(dict(current.expected_state)) == _json(dict(job.expected_state))
                        and _json(dict(current.result)) == _json(expected),
                        "Initial PDF plan effect was not committed by its exact workflow.")
                    self.kernel.revalidate_job_authority(connection, current.id)
                    request, policy = self._request(connection, run.source.preview_id)
                    parent, fence = self._parent(connection, request)
                    _require(policy.policy_version == run.source.plan.policy_version
                        and current.handler_version == policy.handler_version
                        and current.expected_state["version"] == policy.workflow_version
                        and parent.id == run.source.parent_job_id and fence == run.source.fence_sha256
                        and self._source_configuration_matches(run.source.configuration_json, policy))
                    state = self.repo._bound(connection, run)  # Official committed run/archive/source binding.
                    _require(state["status"] == "pending", "Initial PDF run already stopped or completed.")
            finally:
                connection.close()
        cancellation = _ParserCancellation(check_committed)
        return _seed_committed_preparation(self.source, self.archive, run.source, archived,
            check_committed=check_committed, cancel_event=cancellation,
            **({'run': run} if run.source.plan.policy_version in (VERSION_V31, VERSION_V32, VERSION_V35, VERSION_V36) else {}))

    def commit_plan(self, connection, job, prepared):
        _require(type(prepared.payload) is tuple and len(prepared.payload)==2)
        run,archived=prepared.payload
        _require(type(run) is PreparedVisualRun and type(archived) is PreparedPdfVisualArchive
            and archived.source is run.source and archived.metadata[0]==run.source_archive_id
            and run.source.preview_id == job.input_ref["preview_id"])
        request, policy = self._request(connection, run.source.preview_id)
        parent, fence = self._parent(connection, request)
        _require(self._source_configuration_matches(run.source.configuration_json, policy)
            and _json(dict(prepared.result)) == _json(dict(schema_version=1, run_id=run.run_id, batch_count=len(run.batches))))
        self.archive.put(connection,archived)
        self.repo.put_run(connection, run)
        for ordinal, batch_id, *_ in run.batches:
            value = dict(preview_id=request["preview_id"], run_id=run.run_id, ordinal=ordinal, batch_id=batch_id)
            child = self._enqueue(connection, BATCH, value, request, parent, fence, run.source.plan.plan_sha256)
            connection.execute("INSERT INTO pdf_visual_workflow_batch VALUES(?,?,?)", (run.run_id, ordinal, child.id))
        connection.execute("INSERT INTO pdf_visual_workflow_planned VALUES(?,?)", (request["preview_id"], run.run_id))
        self._event(connection, job, "planned", run.run_id, dict(run_id=run.run_id, batch_count=len(run.batches)))

    def _current_effect(self, connection, job):
        current = self.jobs.assert_effect_fence(connection, fence=self._fence(job), now_ms=db_now_ms(connection))
        self.kernel.revalidate_job_authority(connection, current.id)
        self.revalidate_expected_state(connection, current)
        return current

    def _reserve(self, job, run):
        connection = self._factory.open()
        try:
            with transaction(connection):
                self._current_effect(connection, job)
                # A repeated claim invocation cannot use an existing reservation
                # as permission for another remote execution, even after lost ACK.
                prior = connection.execute("SELECT 1 FROM pdf_visual_workflow_claim WHERE job_id=? AND claim_attempt=?",
                    (job.id, job.attempt_count)).fetchone()
                if prior is not None:
                    raise PermanentJobExecutionError("PDF execution was already reserved by this claim.")
                operation = str(uuid5(_NS, f"execution:{job.id}:{job.attempt_count}"))
                reserved = self.repo.start_attempt(connection, run, ordinal=job.input_ref["ordinal"], operation_id=operation)
                _require(reserved.created,"A reservation replay cannot execute the visual model again.")
                attempt=reserved.attempt
                connection.execute("INSERT INTO pdf_visual_workflow_claim VALUES(?,?,?,?)",
                    (attempt.id, job.id, job.claim_owner, job.attempt_count))
                self._event(connection, job, "execution_reserved", run.run_id,
                    dict(run_id=run.run_id, attempt_id=attempt.id, attempt_units=attempt.attempt_units), suffix=attempt.id)
                return attempt
        finally:
            connection.close()

    @staticmethod
    def _cause(error, kinds):
        seen = set()
        while error is not None and id(error) not in seen:
            seen.add(id(error))
            if isinstance(error, kinds):
                return error
            error = error.__cause__ or error.__context__
        return None

    @classmethod
    def _retained_attempt(cls, error):
        """Read bounded canonical evidence without changing or logging its body."""
        stopped = cls._cause(error, (PdfVisualStopped,))
        if stopped is None or type(stopped.attempt_json) is not str:
            return None
        try:
            encoded = stopped.attempt_json
            if len(encoded.encode("utf-8")) > 65_536:
                return None
            evidence = json.loads(encoded)
            if type(evidence) is dict and _json(evidence) == encoded:
                return evidence
        except (ValueError, TypeError, UnicodeError, RecursionError):
            pass
        return None

    @classmethod
    def _failed_provider_attempt(cls, error):
        evidence = cls._retained_attempt(error)
        return (evidence if evidence is not None and evidence.get("status") == "failed"
                and "failure_code" in evidence else None)

    @classmethod
    def _provider_failure_cause(cls, error):
        provider = cls._cause(error, (PdfVisualProviderError, CodexResponseError, ModelResponseError))
        if provider is not None:
            return provider
        evidence = cls._failed_provider_attempt(error)
        code = None if evidence is None else evidence.get("failure_code")
        if type(code) is not str or code not in _PROVIDER_FAILURE_CODES:
            return None
        # The runner can return None after its response parser rejects completion.
        # Preserve the known code instead of mistaking the resulting size error
        # for model-authored syntax. The original envelope remains the cause and
        # its attempt/response bytes stay untouched.
        kind = ModelResponseError if code.startswith("MODEL_") else CodexResponseError
        provider = kind(code, "Provider did not return a complete validated response.")
        provider.__cause__ = error
        return provider

    @classmethod
    def _provider_failure_code(cls, error):
        provider = cls._provider_failure_cause(error)
        code = None if provider is None else provider.code
        return code if type(code) is str and code in _PROVIDER_FAILURE_CODES else None

    @classmethod
    def _provider_failure_diagnostics(cls, error, plan):
        if plan.policy_version not in (POLICY_V20,POLICY_V21,POLICY_V22,POLICY_V23,POLICY_V24,POLICY_V25, POLICY_V26, POLICY_V27, POLICY_V28, POLICY_V29, POLICY_V30, POLICY_V31, POLICY_V32):
            if not (plan.policy_version == POLICY_V36
                    and plan._literal_writer_guidance_version in (LITERAL_CONTEXT_BARE_VERSION, LITERAL_CONTEXT_RELATION_VERSION, LITERAL_CONTEXT_EXPLICIT_TABLE_VERSION, LITERAL_CONTEXT_BOUNDED_OWNED_VERSION,LITERAL_CONTEXT_ADMITTED_OWNED_VERSION)
                    and plan.model_policy.provider_order == ("hynix_api",)
                    and cls._provider_failure_code(error) == "MODEL_RESPONSE_INCOMPLETE"):
                return {}
        provider = cls._provider_failure_cause(error)
        value = getattr(provider, 'provider_failure_diagnostics', None)
        if value is None:
            return {}
        from ...llm_response import validate_chat_failure_diagnostics
        return {'provider_failure_diagnostics': validate_chat_failure_diagnostics(value)}

    @classmethod
    def _execution_failure_code(cls, error):
        if cls._cause(error, (AuthoringWorkLimitError,)):
            return "WORK_LIMIT"
        provider = cls._provider_failure_cause(error)
        if provider is not None:
            if isinstance(provider, ModelResponseError) and provider.code == "MODEL_RESPONSE_HTTP_STATUS":
                if provider.http_status == 408:
                    return "TIMEOUT"
                if provider.http_status == 429 or (
                        provider.http_status is not None and 500 <= provider.http_status <= 599):
                    return "MODEL_FAILURE"
                return "INVALID_OUTPUT"
            return PDF_VISUAL_TRANSIENT_FAILURES.get(provider.code, "INVALID_OUTPUT")
        if cls._cause(error, (AuthoringOutputError, AuthoringIncompleteError)):
            return "INVALID_OUTPUT"
        return "TIMEOUT" if cls._cause(error, (TimeoutError,)) else "MODEL_FAILURE"

    @classmethod
    def _may_retry_local_qwen_output(cls, plan, code, error, sequence):
        """Preserve legacy retry; fresh V14 retries only unbound image syntax."""
        stopped = cls._cause(error, (PdfVisualStopped,))
        evidence = cls._retained_attempt(error)
        if stopped is not None and (evidence is None or evidence.get("status") == "failed"
                                    or "failure_code" in evidence):
            return False
        from ..pdf_visual_authoring import ATOMIC_REQUEST_PARTITION_V14
        future_image_format = (plan.policy_version == POLICY_V36
            and plan.request_partition_policy == ATOMIC_REQUEST_PARTITION_V14
            and stopped is not None and stopped.receipt is not None
            and cls._cause(error, (UnboundMarkdownImageOutputError,)) is not None)
        legacy_local_format = (plan.policy_version in (POLICY_V4, POLICY_V5, POLICY_V6, POLICY_V7, POLICY_V8, POLICY_V9, POLICY_V10, POLICY_V11, POLICY_V12)
            and plan.model_policy.provider_order == ("hynix_api",)
            and plan.model_policy.model_ids == ("qwen36-local",))
        return (code == "INVALID_OUTPUT"
            and (legacy_local_format or future_image_format)
            and cls._cause(error, (PdfVisualProviderError, CodexResponseError, ModelResponseError)) is None
            and cls._cause(error, (AuthoringOutputError,)) is not None
            and sequence == 1)

    def prepare_batch(self, job):
        return self.kernel.prepare_with_read_guard(self._fence(job),
            lambda preflight: self._prepare_batch(job, preflight))

    def _prepare_batch(self, job, preflight):
        fence = self._fence(job)
        # Keep the initial guard independent until the existing restore read
        # establishes ordinary/protected source configuration. No classification
        # read is inserted ahead of the original claim/authority check.
        self.kernel.preflight_preparation(fence)
        self._check_protected_rights(actor_user_id=job.actor_user_id,
            preview_id=job.input_ref["preview_id"])
        run = self._restore(job, _preflight=preflight)
        try:
            ordinary_batch = (run.source.plan.policy_version in (POLICY_V31, POLICY_V32, POLICY_V33, POLICY_V34, POLICY_V35, POLICY_V36)
                and "protected_binding" not in json.loads(run.source.configuration_json))
            def batch_preflight(*, deadline_at_db_ms):
                if ordinary_batch:
                    return preflight(deadline_at_db_ms=deadline_at_db_ms)
                # Protected/historical provider callbacks retain their original
                # independent physical guard lifetime, even in the normal host.
                return self.kernel.preflight_preparation(fence, deadline_at_db_ms=deadline_at_db_ms)
            timeout = run.source.plan.execution_policy.timeout_s
            # Lease lasts through the bounded call plus commit margin. This is
            # an explicit renewal, unlike preflight, and cannot revive a lost claim.
            self.kernel.renew_claim(fence, lease_ms=(timeout + 30) * 1000)
            attempt = self._reserve(job, run)

            def guard():
                try:
                    batch_preflight(deadline_at_db_ms=attempt.started_at + timeout * 1000)
                    self._check_protected_rights(actor_user_id=job.actor_user_id,
                        preview_id=job.input_ref["preview_id"])
                except JobPreparationDeadlineError as exc:
                    raise TimeoutError("PDF execution deadline reached.") from exc

            try:
                receipt = self.author.author_batch(run.source.plan, attempt.ordinal, attempt_guard=guard,
                    **({'expected_request_sha256': attempt.request_sha256}
                       if run.source.plan.policy_version in (POLICY_V31, POLICY_V32) else {}))
                outcome = prepare_visual_outcome(run, attempt, receipt=receipt)
                guard()
            except Exception as exc:
                guard_error = self._cause(exc, _GUARD_ERRORS)
                if guard_error is not None:
                    raise guard_error
                code = self._execution_failure_code(exc)
                provider_failure_code = self._provider_failure_code(exc)
                reported_cause = self._provider_failure_cause(exc) if code != "WORK_LIMIT" else None
                retained = exc.receipt if isinstance(exc, PdfVisualStopped) else None
                evidence = exc.attempt_json if isinstance(exc,PdfVisualStopped) else "{}"
                failure = prepare_visual_outcome(run, attempt, receipt=retained, status="failed", failure_code=code,
                    attempt_json=evidence)
                connection = self._factory.open()
                try:
                    with transaction(connection):
                        self._current_effect(connection, job)
                        self.repo.put_outcome(connection, run, failure)
                        self._event(connection, job, "execution_failed", run.run_id,
                            dict(run_id=run.run_id, attempt_id=attempt.id, failure_code=code,
                                **self._provider_failure_diagnostics(exc, run.source.plan),
                                **({"provider_failure_code": provider_failure_code}
                                   if provider_failure_code is not None else {})), suffix=attempt.id)
                finally:
                    connection.close()
                local_qwen_format_retry = self._may_retry_local_qwen_output(
                    run.source.plan, code, exc, attempt.sequence)
                if code == "WORK_LIMIT" or (code == "INVALID_OUTPUT" and not local_qwen_format_retry):
                    raise PermanentJobExecutionError("Visual output or budget failed; no semantic repair/retry is permitted.") from (reported_cause or exc)
                raise RetryableJobExecutionError(
                    "Visual transport or local-Qwen output format failed within its reserved retry budget."
                ) from (reported_cause or exc)
            result = dict(schema_version=1, run_id=run.run_id, ordinal=attempt.ordinal, attempt_id=attempt.id)
            return PreparedJobEffect(result=result, payload=(run, outcome), cleanup=run.source.close)
        except BaseException as exc:
            try:
                if isinstance(exc, AuthoringWorkLimitError):
                    self._record_work_limit(job)
            finally:
                run.source.close()
            raise

    def commit_batch(self, connection, job, prepared):
        _require(type(prepared.payload) is tuple and len(prepared.payload) == 2)
        run, outcome = prepared.payload
        _require(type(run) is PreparedVisualRun and type(outcome) is PreparedVisualOutcome)
        expected = dict(schema_version=1, run_id=job.input_ref["run_id"], ordinal=job.input_ref["ordinal"],
            attempt_id=outcome.attempt.id)
        binding = connection.execute("SELECT job_id,claim_owner,claim_attempt FROM pdf_visual_workflow_claim WHERE attempt_id=?",
            (outcome.attempt.id,)).fetchone()
        _require(run.run_id == expected["run_id"] and outcome.attempt.ordinal == expected["ordinal"]
            and outcome.metadata[3] == "validated" and binding is not None
            and tuple(binding) == (job.id, job.claim_owner, job.attempt_count)
            and _json(dict(prepared.result)) == _json(expected))
        self.repo.put_outcome(connection, run, outcome)
        self._event(connection, job, "batch_completed", run.run_id, expected)

    def prepare_completion(self, job):
        return self.kernel.prepare_with_read_guard(self._fence(job),
            lambda preflight: self._prepare_completion(job, preflight))

    def _prepare_completion(self, job, preflight):
        fence = self._fence(job)
        # Preserve independent admission before restore classifies the source.
        self.kernel.preflight_preparation(fence)
        self._check_protected_rights(actor_user_id=job.actor_user_id,
            preview_id=job.input_ref["preview_id"])
        run = self._restore(job, _preflight=preflight)
        try:
            ordinary_completion = (run.source.plan.policy_version in (POLICY_V31, POLICY_V32, POLICY_V33, POLICY_V34, POLICY_V35, POLICY_V36)
                and "protected_binding" not in json.loads(run.source.configuration_json))
            connection = self._factory.open(read_only=True)
            try:
                with transaction(connection, TransactionMode.DEFERRED):
                    receipts = self.repo.load_receipts(connection, run)
            finally:
                connection.close()
            with self._parser_guard(job, _preflight=preflight if ordinary_completion else None) as cancellation:
                snapshot = prepare_visual_snapshot(run, receipts,cancel_event=cancellation)
            self.kernel.preflight_preparation(fence)
            self._check_protected_rights(actor_user_id=job.actor_user_id,
                preview_id=job.input_ref["preview_id"])
            return PreparedJobEffect(result=dict(schema_version=1, run_id=run.run_id,
                snapshot_id=snapshot.snapshot_id, content_sha256=_hash(snapshot.markdown),
                manifest_sha256=_hash(snapshot.manifest_json)), payload=(run, snapshot), cleanup=run.source.close)
        except BaseException as exc:
            try:
                if isinstance(exc, AuthoringWorkLimitError):
                    self._record_work_limit(job)
            finally:
                run.source.close()
            raise

    def _record_work_limit(self, job):
        connection = self._factory.open()
        try:
            with transaction(connection):
                self._current_effect(connection, job)
                connection.execute("INSERT INTO pdf_visual_workflow_rejection VALUES(?,?,'WORK_LIMIT',?)",
                    (job.id, job.attempt_count, db_now_ms(connection)))
                self._event(connection, job, "work_rejected", job.input_ref["preview_id"],
                    dict(failure_code="WORK_LIMIT"), suffix=str(job.attempt_count))
        finally:
            connection.close()

    def commit_completion(self, connection, job, prepared):
        _require(type(prepared.payload) is tuple and len(prepared.payload) == 2)
        run, snapshot = prepared.payload
        _require(type(run) is PreparedVisualRun and type(snapshot) is PreparedVisualSnapshot)
        expected = dict(schema_version=1, run_id=job.input_ref["run_id"], snapshot_id=snapshot.snapshot_id,
            content_sha256=_hash(snapshot.markdown), manifest_sha256=_hash(snapshot.manifest_json))
        _require(run.run_id == expected["run_id"] and _json(dict(prepared.result)) == _json(expected))
        self.repo.put_snapshot(connection, run, snapshot)
        self._event(connection, job, "completed", run.run_id, expected)

    @staticmethod
    def _completed_batches(connection, run_id):
        return connection.execute("""SELECT count(*) FROM pdf_visual_workflow_batch w
            JOIN durable_job j ON j.id=w.job_id JOIN pdf_visual_authoring_outcome o
              ON o.run_id=w.run_id AND o.ordinal=w.ordinal AND o.status='validated'
            WHERE w.run_id=? AND j.state='succeeded'""", (run_id,)).fetchone()[0]

    def _abandon(self, connection, attempt_id, code):
        """Metadata-only cleanup; SQL requires expired claim or exact run stop.

No source/config/response read and no authorization to create a new effect.
Open attempts cannot be silently forgotten or reused as free executions.
"""
        row = connection.execute("SELECT * FROM pdf_visual_authoring_attempt WHERE id=?", (attempt_id,)).fetchone()
        _require(row is not None)
        if connection.execute("SELECT 1 FROM pdf_visual_authoring_outcome WHERE attempt_id=?", (attempt_id,)).fetchone():
            return
        connection.execute("""INSERT INTO pdf_visual_authoring_outcome
            (attempt_id,run_id,ordinal,status,failure_code,response_sha256,response_size_bytes,
             model_sha256,model_size_bytes,transport_sha256,transport_size_bytes,
             evidence_sha256,evidence_size_bytes,evidence_binding,completed_at)
            VALUES(?,?,?,'abandoned',?,?,?,?,?,?,?,?,?,?,?)""",
            (attempt_id, row["run_id"], row["ordinal"], code, _hash(""), 0,
                _hash("{}"), 2, _hash("{}"), 2, _hash("{}"), 2, "absent", db_now_ms(connection)))
        connection.execute("""INSERT INTO pdf_visual_authoring_outcome_body
            (attempt_id,response,model_json,transport_json,attempt_json) VALUES(?,'','{}','{}','{}')""", (attempt_id,))
        bound = connection.execute("SELECT job_id FROM pdf_visual_workflow_claim WHERE attempt_id=?", (attempt_id,)).fetchone()
        self._event(connection, self.jobs.require(connection, bound[0]), "execution_abandoned", row["run_id"],
            dict(run_id=row["run_id"], attempt_id=attempt_id, failure_code=code), suffix=attempt_id)

    def _stop(self, connection, preview_id, job, code):
        """Handler-owned convergence only. It can terminate, never publish/read.

Exact immutable job-to-preview binding is verified by SQL. Kernel invokes this
inside the same transaction as its terminal CAS; a stale claim rolls it back.
"""
        state = connection.execute("""SELECT r.id,s.status FROM pdf_visual_authoring_run r
            JOIN pdf_visual_authoring_state s ON s.run_id=r.id WHERE r.preview_id=?""", (preview_id,)).fetchone()
        if state is not None and state["status"] == "ready":
            return
        stopped = connection.execute("SELECT failure_code FROM pdf_visual_workflow_stop WHERE preview_id=?", (preview_id,)).fetchone()
        if stopped is None:
            connection.execute("INSERT INTO pdf_visual_workflow_stop VALUES(?,?,?,?)", (preview_id, job.id, code, db_now_ms(connection)))
            self._event(connection, job, "stopped", preview_id, dict(preview_id=preview_id, failure_code=code))
        else:
            code = stopped[0]
        if state is not None and state["status"] == "pending":
            rows = connection.execute("""SELECT a.id FROM pdf_visual_authoring_attempt a
                LEFT JOIN pdf_visual_authoring_outcome o ON o.attempt_id=a.id
                WHERE a.run_id=? AND o.attempt_id IS NULL ORDER BY a.ordinal,a.sequence""", (state[0],)).fetchall()
            for row in rows:
                self._abandon(connection, row[0], code)
            connection.execute("""UPDATE pdf_visual_authoring_state SET status='failed',failure_code=?,completed_at=?,
                row_version=row_version+1 WHERE run_id=? AND status='pending'""", (code, db_now_ms(connection), state[0]))
        siblings = connection.execute("""SELECT job_id FROM pdf_visual_workflow_plan WHERE preview_id=?
            UNION ALL SELECT w.job_id FROM pdf_visual_workflow_batch w JOIN pdf_visual_authoring_run r ON r.id=w.run_id WHERE r.preview_id=?
            UNION ALL SELECT w.job_id FROM pdf_visual_workflow_completion w JOIN pdf_visual_authoring_run r ON r.id=w.run_id WHERE r.preview_id=?""",
            (preview_id, preview_id, preview_id)).fetchall()
        for row in siblings:
            current = self.jobs.require(connection, row[0])
            if current.id != job.id and current.state in _ACTIVE and not current.cancel_requested:
                self.jobs.request_cancel(connection, job_id=current.id, now_ms=db_now_ms(connection))
                self._event(connection, current, "sibling_cancel_requested", preview_id,
                    dict(preview_id=preview_id, stopped_by_job_id=job.id, failure_code=code))

    def prepare_terminal(self, connection, job, terminal_state):
        version, handler, _ = self._request_identity(connection, job.input_ref["preview_id"])
        _require(job.job_type in (PLAN, BATCH, COMPLETE) and job.handler_version == handler
            and job.expected_state.get("version") == version)
        code = "SOURCE_UNAVAILABLE"
        rejection = connection.execute("SELECT failure_code FROM pdf_visual_workflow_rejection WHERE job_id=? AND claim_attempt=?",
            (job.id, job.attempt_count)).fetchone()
        failure = connection.execute("""SELECT o.failure_code FROM pdf_visual_workflow_claim c
            JOIN pdf_visual_authoring_outcome o ON o.attempt_id=c.attempt_id
            WHERE c.job_id=? AND c.claim_attempt=? AND o.status<>'validated'""", (job.id, job.attempt_count)).fetchone()
        if terminal_state == "cancelled":
            code = "CANCELLED"
        elif rejection is not None:
            code = rejection[0]
        elif failure is not None:
            code = failure[0]
        elif job.claim_expires_at_ms is not None and job.claim_expires_at_ms <= db_now_ms(connection):
            code = "LEASE_LOST"
        else:
            try:
                self.kernel.revalidate_job_authority(connection, job.id)
            except JobAuthorizationRejectedError:
                code = "AUTHORITY_UNAVAILABLE"
        self._stop(connection, job.input_ref["preview_id"], job, code)

    def converge_terminal(self, connection, job):
        if job.state in ("failed", "cancelled", "dead"):
            self.prepare_terminal(connection, job, job.state)

    def reconcile_pending(self, connection):
        # Match the kernel's recover-all scope exactly: a smaller LIMIT here
        # would leave some expired attempts open after their jobs enter retry.
        expired = connection.execute("""SELECT a.id FROM pdf_visual_workflow_claim c
            JOIN pdf_visual_authoring_attempt a ON a.id=c.attempt_id JOIN durable_job j ON j.id=c.job_id
            LEFT JOIN pdf_visual_authoring_outcome o ON o.attempt_id=a.id
            WHERE o.attempt_id IS NULL AND j.state IN ('claimed','running') AND j.claim_expires_at<=?
            ORDER BY j.claim_expires_at,j.id""", (db_now_ms(connection),)).fetchall()
        for row in expired:
            self._abandon(connection, row[0], "LEASE_LOST")

        cursor = connection.execute("SELECT last_preview_id FROM pdf_visual_workflow_scan WHERE singleton=1").fetchone()[0]
        # A persistent circular cursor prevents the first 16 long-running jobs
        # from starving every later upload. It carries no access authority.
        pending = connection.execute("""SELECT q.preview_id,q.parent_job_id FROM pdf_visual_workflow_request q
            LEFT JOIN pdf_visual_authoring_run r ON r.preview_id=q.preview_id
            LEFT JOIN pdf_visual_authoring_state s ON s.run_id=r.id
            WHERE (s.status IS NULL OR s.status='pending')
              AND NOT EXISTS(SELECT 1 FROM pdf_visual_workflow_stop WHERE preview_id=q.preview_id)
            ORDER BY CASE WHEN q.preview_id>? THEN 0 ELSE 1 END,q.preview_id LIMIT 16""", (cursor,)).fetchall()
        if pending:
            connection.execute("UPDATE pdf_visual_workflow_scan SET last_preview_id=? WHERE singleton=1", (pending[-1][0],))
        for row in pending:
            parent = self.jobs.require(connection, row["parent_job_id"])
            preview = connection.execute("SELECT status,expires_at FROM publication_preview WHERE id=?", (row[0],)).fetchone()
            if parent.state in ("failed", "cancelled", "dead") or parent.cancel_requested or preview[0] not in ("queued", "extracting", "ready") or preview[1] <= db_now_ms(connection):
                self._stop(connection, row[0], parent,
                    "CANCELLED" if parent.cancel_requested or parent.state == "cancelled" else "SOURCE_UNAVAILABLE")
                continue
            try:
                self.kernel.revalidate_job_authority(connection, parent.id)
            except JobAuthorizationRejectedError:
                self._stop(connection, row[0], parent, "AUTHORITY_UNAVAILABLE")
                continue
            terminal = connection.execute("""SELECT job_id FROM pdf_visual_workflow_plan w JOIN durable_job j ON j.id=w.job_id
                WHERE w.preview_id=? AND (j.state IN ('failed','cancelled','dead') OR j.cancel_requested=1)
                UNION ALL SELECT w.job_id FROM pdf_visual_workflow_batch w JOIN pdf_visual_authoring_run r ON r.id=w.run_id
                JOIN durable_job j ON j.id=w.job_id WHERE r.preview_id=? AND (j.state IN ('failed','cancelled','dead') OR j.cancel_requested=1)
                UNION ALL SELECT w.job_id FROM pdf_visual_workflow_completion w JOIN pdf_visual_authoring_run r ON r.id=w.run_id
                JOIN durable_job j ON j.id=w.job_id WHERE r.preview_id=? AND (j.state IN ('failed','cancelled','dead') OR j.cancel_requested=1)
                LIMIT 1""", (row[0], row[0], row[0])).fetchone()
            if terminal is not None:
                current = self.jobs.require(connection, terminal[0])
                self.prepare_terminal(connection, current, "cancelled" if current.cancel_requested else current.state)
                continue
            if parent.state != "succeeded":
                continue
            try:
                request, _ = self._request(connection, row[0])
                parent, fence = self._parent(connection, request)
            except (JobAuthorizationRejectedError, JobExpectedStateConflictError, ValueError, AuthoringError, PreservationStorageConflict, PdfVisualStorageConflict):
                self._stop(connection, row[0], parent, "SOURCE_UNAVAILABLE")
                continue
            if connection.execute("SELECT 1 FROM pdf_visual_workflow_plan WHERE preview_id=?", (row[0],)).fetchone() is None:
                queued = self._enqueue(connection, PLAN, dict(preview_id=row[0]), request, parent, fence)
                connection.execute("INSERT INTO pdf_visual_workflow_plan VALUES(?,?,?)", (row[0], queued.id, fence))
                self._event(connection, queued, "plan_queued", row[0], dict(preview_id=row[0]))
                continue
            run = connection.execute("SELECT * FROM pdf_visual_authoring_run WHERE preview_id=?", (row[0],)).fetchone()
            if run is not None and not connection.execute("SELECT 1 FROM pdf_visual_workflow_completion WHERE run_id=?", (run["id"],)).fetchone():
                if self._completed_batches(connection, run["id"]) == run["batch_count"]:
                    value = dict(preview_id=row[0], run_id=run["id"])
                    queued = self._enqueue(connection, COMPLETE, value, request, parent, fence, run["source_plan_sha256"])
                    connection.execute("INSERT INTO pdf_visual_workflow_completion VALUES(?,?)", (run["id"], queued.id))
                    self._event(connection, queued, "completion_queued", run["id"], dict(run_id=run["id"]))

    def _event(self, connection, job, action, aggregate_id, details, *, suffix=""):
        request = connection.execute("SELECT preview_id FROM pdf_visual_workflow_request WHERE parent_job_id=?",
            (job.id,)).fetchone()
        preview_id = request[0] if request is not None else job.input_ref["preview_id"]
        version, _, _ = self._request_identity(connection, preview_id)
        actor = UserRepository().require(connection, job.actor_user_id)
        name, now = "wiki.pdf_visual." + action, db_now_ms(connection)
        key = f"{version}:{job.id}:{action}:{suffix}"
        payload = {**details, "schema_version": 1, "job_id": job.id}
        AuditRepository().append(connection, AuditEventDraft(event_uuid=str(uuid5(_NS, key)), occurred_at_ms=now,
            request_id=job.id, operation_id=job.operation_id, action=name, outcome="success",
            actor_kind="user", actor_user_id=actor.id, actor_display_name=actor.display_name,
            actor_role_snapshot=actor.system_role, space_id=job.space_id, details=payload))
        OutboxRepository().append(connection, OutboxEventDraft(logical_key=key, space_id=job.space_id,
            aggregate_type="pdf_visual_authoring_run", aggregate_id=aggregate_id, aggregate_version=1,
            event_type=name, payload=payload, available_at_ms=now))

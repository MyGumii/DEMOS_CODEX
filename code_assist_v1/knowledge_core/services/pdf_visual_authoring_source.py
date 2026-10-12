"""Canonical retained-PDF visual source preparation; no model or publication.

All retained original/embedded CAS objects stay leased until the caller closes
the preparation. Page pixels are derived ONLY from the verified original PDF.
Existing embedded assets are verified as retained inventory, not asserted to be
reconstructed from the original. Revalidation is SQL/CPU only; parser/image I/O
and plan admission occur outside SQLite transactions. No old v1 guard is relaxed.
"""
from dataclasses import asdict, dataclass, field, replace
from hashlib import sha256
import hmac
import json
import secrets
from uuid import uuid5

from ...document_pdf_geometry import PdfGeometryLimits
from ...document_pdf_process import PdfProcessPolicy, PdfProcessError
from ..pdf_visual_source import PdfPageRenderPolicy, prepare_pdf_visual_source, renderer_identity
from ..protected_pdf_open import AuthorizedOwnerPdfOpener
from ..protected_pdf_visual_source import prepare_authorized_pdf_visual_source
from ..migrations.protected_extraction_provenance_catalog import PROTECTED_EXTRACTION_PROVENANCE_MIGRATION_CATALOG
from ..pdf_visual_authoring import PdfVisualPlan, VERSION as WRITING_VERSION, VERSION_V12, VERSION_V13, VERSION_V14, VERSION_V15, VERSION_V16, VERSION_V17, VERSION_V18, VERSION_V19, VERSION_V20, VERSION_V21, VERSION_V22, VERSION_V23, VERSION_V24, VERSION_V25, VERSION_V26, VERSION_V27, VERSION_V28, VERSION_V29, VERSION_V30, VERSION_V31, VERSION_V32, prompt_for_policy
from ..pdf_native_figure_source import prepare_native_figures
from ..pdf_visual_authoring import VERSION_V33, VERSION_V34, VERSION_V35, VERSION_V36, ATOMIC_REQUEST_PARTITION_V14
from ..pdf_layout_source import prepare_layout
from ..pdf_chart_preparation import (
    VERSION as CHART_PREPARATION_VERSION, PdfChartPreparationLimits,
    prepare_pdf_chart_supplement, CONDITIONAL_WRITER_INPUT_VERSION as CHART_WRITER_INPUT_VERSION,
    SOURCE_GEOMETRY_INPUT_VERSION, AXIS_FRAGMENT_RECIPE_VERSION, LITERAL_CONTEXT_VERSION, LITERAL_CONTEXT_WRITER_VERSION, LITERAL_CONTEXT_TABLE_VERSION, LITERAL_CONTEXT_NATIVE_VERSION, LITERAL_CONTEXT_KEY_VERSION, LITERAL_CONTEXT_CLAUSE_VERSION, LITERAL_CONTEXT_BARE_VERSION, LITERAL_CONTEXT_RELATION_VERSION, LITERAL_CONTEXT_EXPLICIT_TABLE_VERSION, LITERAL_CONTEXT_BOUNDED_OWNED_VERSION, LITERAL_CONTEXT_ADMITTED_OWNED_VERSION,
)
from ..pdf_native_chart_input import _LANGUAGE as _CHART_LANGUAGE
from ..pdf_figure_roi_source import prepare_figure_rois, figure_roi_group_identity
from ..wiki_authoring import AuthoringModelPolicy, AuthoringWorkPolicy, _hash, _json
from ..wiki_preservation import PreservationExecutionPolicy
from .pdf_authoring_source import CanonicalPdfAuthoringSourceService, PdfSourceConflict, _require, _typed_plan_digest
from .native_authoring_source import _Leases
from .publication import StagedVerifiedBlob, _EMBEDDED_ASSET_NAMESPACE

PDF_VISUAL_PREPARATION_VERSION = "canonical-pdf-visual-authoring-source-v1"
_KEY = secrets.token_bytes(32)


def _require_numeric_append_catalog(factory):
    """V36 alone requires the exact verified v95 successor, never a v94 label."""
    from ..migrations.pdf_numeric_append_catalog import PDF_NUMERIC_APPEND_MIGRATION_CATALOG
    expected = PDF_NUMERIC_APPEND_MIGRATION_CATALOG.migrations
    catalog = getattr(factory, "migration_catalog", None)
    _require(catalog is not None and catalog.migrations[:len(expected)] == expected,
        "Numeric append requires its verified v95 successor catalog.")


def _require_atomic_partition_catalog(factory):
    """Explicit V14 producer/store protocol, never a migration side effect."""
    from ..migrations.pdf_atomic_request_partition_catalog import PDF_ATOMIC_REQUEST_PARTITION_MIGRATION_CATALOG
    expected = PDF_ATOMIC_REQUEST_PARTITION_MIGRATION_CATALOG.migrations
    catalog = getattr(factory, 'migration_catalog', None)
    _require(catalog is not None and catalog.migrations[:len(expected)] == expected,
        'Original-range partition requires its verified v96 successor catalog.')


def forward_source_descriptor(render_policy):
    return dict(version=LITERAL_CONTEXT_ADMITTED_OWNED_VERSION,
        configured_render_policy=asdict(render_policy),
        configured_request_budget_bytes=1_114_112,effective_request_budget_bytes=4_194_304,
        configured_logical_input_bytes=67_108_864,effective_logical_input_bytes=134_217_728,
        all_pages_retained=True,grid_fidelity_proven=False)


@dataclass(frozen=True, slots=True)
class PdfRetainedAssetPolicy:
    maximum_assets: int = 64
    maximum_asset_bytes: int = 16_777_216
    maximum_total_bytes: int = 67_108_864

    def __post_init__(self):
        ceilings = (64,16_777_216,67_108_864)
        _require(all(type(v) is int and 1 <= v <= maximum for v,maximum in zip(asdict(self).values(),ceilings)),
            "Invalid retained PDF asset policy.")


@dataclass(frozen=True, slots=True)
class PreparedCanonicalPdfVisualSource:
    preview_id: str
    actor_user_id: str
    parent_job_id: str
    artifact_id: str
    plan: PdfVisualPlan = field(repr=False)
    fence_sha256: str
    configuration_json: str
    retained_assets_json: str
    seal: str = field(repr=False)
    _leases: _Leases = field(repr=False,compare=False)
    source_preparation_version: str = PDF_VISUAL_PREPARATION_VERSION

    def close(self): self._leases.close()

    def __enter__(self):
        _require(self._leases.token in self._leases.owner._active,"Visual PDF preparation is closed.")
        return self

    def __exit__(self,*_): self.close()


def _seal(prepared):
    _require(type(prepared) is PreparedCanonicalPdfVisualSource
        and prepared.source_preparation_version == PDF_VISUAL_PREPARATION_VERSION,
        "Visual PDF source preparation policy changed.")
    # Geometry bytes <=32MiB, rendered PNG bytes <=64MiB. Bound cumulative
    # typed hashing too; caches are included, not only the convenient plan ID.
    if prepared.plan.policy_version in (VERSION_V12,VERSION_V13,VERSION_V14,VERSION_V15, VERSION_V16, VERSION_V17, VERSION_V18, VERSION_V19, VERSION_V20, VERSION_V21, VERSION_V22, VERSION_V23, VERSION_V24, VERSION_V25, VERSION_V26, VERSION_V27, VERSION_V28, VERSION_V29, VERSION_V30, VERSION_V31, VERSION_V32, VERSION_V34, VERSION_V35, VERSION_V36):
        # Unique geometry, page/native/crop/job PNGs remain bounded, without
        # repeatedly hashing the same source through nested forward plans.
        digest = _typed_plan_digest(prepared.plan,prompt_for_policy(prepared.plan.policy_version),expected_type=PdfVisualPlan,
            maximum_hashed_bytes=335_544_320,maximum_text_bytes=268_435_456,deduplicate=True)
    else:
        digest = _typed_plan_digest(prepared.plan,prompt_for_policy(prepared.plan.policy_version),expected_type=PdfVisualPlan,
            maximum_hashed_bytes=100_663_296)
    record = {"version":prepared.source_preparation_version,"preview_id":prepared.preview_id,
        "actor_user_id":prepared.actor_user_id,"parent_job_id":prepared.parent_job_id,
        "artifact_id":prepared.artifact_id,"fence_sha256":prepared.fence_sha256,
        "configuration_json":prepared.configuration_json,"retained_assets_json":prepared.retained_assets_json,
        "private_plan_sha256":digest,"lease_token":prepared._leases.token}
    return hmac.digest(_KEY,_json(record).encode(),"sha256").hex()


class CanonicalPdfVisualSourceService(CanonicalPdfAuthoringSourceService):
    def __init__(self,factory,scope_resolver,stager,*,python_executable,
            geometry_limits=PdfGeometryLimits(),process_policy=PdfProcessPolicy(),
            render_policy=PdfPageRenderPolicy(),asset_policy=PdfRetainedAssetPolicy(),
            protected_pdf_opener: AuthorizedOwnerPdfOpener | None = None,
            chart_ocr_language: str | None = None, chart_limits=PdfChartPreparationLimits(),
            allow_thin_component_bands=False, allow_literal_definition_contexts=False, allow_native_text_presentation=False, allow_key_information_figures=False,
            fresh_key_information_version=LITERAL_CONTEXT_KEY_VERSION):
        _require(type(allow_thin_component_bands) is bool, 'Explicit bool source geometry selection required.')
        _require(type(allow_literal_definition_contexts) is bool, 'Explicit bool literal context selection required.')
        _require(type(render_policy) is PdfPageRenderPolicy and type(asset_policy) is PdfRetainedAssetPolicy,
            "Explicit visual and retained-asset policies are required.")
        _require(protected_pdf_opener is None or type(protected_pdf_opener) is AuthorizedOwnerPdfOpener,
            "An approved protected PDF opener is required.")
        _require(type(chart_limits) is PdfChartPreparationLimits
            and (chart_ocr_language is None or type(chart_ocr_language) is str
                and _CHART_LANGUAGE.fullmatch(chart_ocr_language) is not None),
            "An explicit chart OCR language and typed preparation limits are required.")
        super().__init__(factory,scope_resolver,stager,python_executable=python_executable,
            geometry_limits=geometry_limits,process_policy=process_policy)
        self._render_policy,self._asset_policy = render_policy,asset_policy
        self._protected_pdf_opener = protected_pdf_opener
        self._chart_ocr_language, self._chart_limits = chart_ocr_language, chart_limits
        self._allow_thin_component_bands = allow_thin_component_bands
        _require(type(allow_native_text_presentation) is bool and (not allow_native_text_presentation
            or allow_literal_definition_contexts), "Native presentation requires the explicit literal input gate.")
        _require(type(allow_key_information_figures) is bool and (not allow_key_information_figures
            or (allow_native_text_presentation and allow_literal_definition_contexts)),
            "Key-information requires explicit native and literal input gates.")
        _require(type(fresh_key_information_version) is str
            and fresh_key_information_version in (LITERAL_CONTEXT_KEY_VERSION,LITERAL_CONTEXT_CLAUSE_VERSION,LITERAL_CONTEXT_BARE_VERSION,LITERAL_CONTEXT_RELATION_VERSION,LITERAL_CONTEXT_EXPLICIT_TABLE_VERSION,LITERAL_CONTEXT_BOUNDED_OWNED_VERSION,LITERAL_CONTEXT_ADMITTED_OWNED_VERSION)
            and (fresh_key_information_version == LITERAL_CONTEXT_KEY_VERSION or allow_key_information_figures),
            "Explicit fresh key-information version requires its input gate.")
        self._fresh_key_information_version = fresh_key_information_version
        self._allow_key_information_figures = allow_key_information_figures
        self._allow_native_text_presentation = allow_native_text_presentation
        self._allow_literal_definition_contexts = allow_literal_definition_contexts
        # Pure bounded V12 plans only; no live CAS lease or caller preparation.
        from .pdf_visual_plan_reuse import _PdfVisualPlanReuse
        self._visual_plan_reuse = _PdfVisualPlanReuse(self)

    def _source_extension(self,connection,source,extraction):
        if (extraction["extractor_name"],extraction["extractor_version"]) == (
                "canonical-pypdf","rich-source-v3-bounded-tables"):
            return None
        _require(self._protected_pdf_opener is not None
            and self._factory.migration_catalog.migrations[:len(
                PROTECTED_EXTRACTION_PROVENANCE_MIGRATION_CATALOG.migrations)]
                == PROTECTED_EXTRACTION_PROVENANCE_MIGRATION_CATALOG.migrations
            and (extraction["extractor_name"],extraction["extractor_version"]) == (
                "canonical-approved-owner-pdf","protected-pdf-source-v1"),
            "Protected PDF visual source lacks its approved extraction route.")
        row = connection.execute("SELECT * FROM protected_extraction_provenance WHERE extraction_snapshot_id=?",
            (extraction["id"],)).fetchone()
        _require(row is not None,"Protected PDF visual source lacks retained provenance.")
        bound = dict(row)
        try:
            coverage = json.loads(bound["coverage_json"])
            valid_coverage = (type(coverage) is list
                and len(coverage) == bound["expected_locations"]
                and all(type(item) is dict
                    and set(item) == {"ordinal","status","visual_present","visual_examined"}
                    and type(item["ordinal"]) is int and item["ordinal"] == ordinal
                    and item["status"] == "complete"
                    and type(item["visual_present"]) is bool
                    and item["visual_examined"] is False
                    for ordinal,item in enumerate(coverage,1))
                and _json(coverage) == bound["coverage_json"])
        except (ValueError,TypeError,KeyError,UnicodeError):
            valid_coverage = False
        _require(bound["artifact_id"] == source["id"]
            and bound["original_sha256"] == source["raw_sha256"]
            and type(bound["derivative_sha256"]) is str
            and bound["derivative_sha256"] != source["raw_sha256"]
            and bound["source_kind"] == "pdf_standard_encryption"
            and bound["opening_method"] == "standard_pdf_owner_password_v1"
            and bound["extraction_method"] == "pypdf-source-text-v1"
            and bound["location_kind"] == "page"
            and bound["visual_coverage"] == "not_proven"
            and bound["created_at"] == extraction["created_at"]
            and valid_coverage,
            "Protected PDF visual source provenance differs.")
        return bound

    def _retained_blobs(self,connection,source,extraction,ids):
        # Limit includes one extra row to detect overflow, never a prefix view.
        rows = tuple(dict(row) for row in connection.execute(
            """SELECT b.*,bb.content_sha256 AS retained_sha256,bb.created_at AS retained_created_at,
                o.sha256 AS object_sha256,o.size_bytes AS object_size_bytes,o.relative_path,o.verification_status
                FROM artifact_blob b LEFT JOIN artifact_blob_body bb ON bb.artifact_blob_id=b.id
                LEFT JOIN blob_object o ON o.sha256=bb.content_sha256
                WHERE b.artifact_id=? ORDER BY b.ordinal,b.id LIMIT ?""",
            (source["id"],self._asset_policy.maximum_assets+2)))
        _require(1 <= len(rows) <= self._asset_policy.maximum_assets+1,
            "Visual PDF retained inventory is missing or exceeds its bound.")
        total = 0
        for ordinal,row in enumerate(rows):
            _require(type(row["ordinal"]) is int and row["ordinal"] == ordinal
                and row["role"] == ("raw" if ordinal == 0 else "embedded_asset")
                and row["body_retention_status"] == "present"
                and type(row["content_sha256"]) is str and len(row["content_sha256"]) == 64
                and all(c in "0123456789abcdef" for c in row["content_sha256"])
                and row["content_sha256"] == row["retained_sha256"] == row["object_sha256"]
                and type(row["size_bytes"]) is int and type(row["object_size_bytes"]) is int
                and 0 < row["size_bytes"] == row["object_size_bytes"]
                and type(row["created_at"]) is int and type(row["retained_created_at"]) is int
                and row["created_at"] >= 0
                and row["verification_status"] == "verified"
                and row["relative_path"] == f"{row['content_sha256'][:2]}/{row['content_sha256'][2:4]}/{row['content_sha256']}",
                "Visual PDF retained inventory is incomplete or changed.")
            _require(all(type(row[k]) is str and 0 < len(row[k]) <= size
                    and row[k].strip() == row[k] and not any(ord(c) < 32 for c in row[k])
                    for k,size in (("logical_name",500),("media_type",255))),
                "Visual PDF retained asset labels are invalid.")
            if ordinal:
                total += row["size_bytes"]
                _require(row["size_bytes"] <= self._asset_policy.maximum_asset_bytes
                    and total <= self._asset_policy.maximum_total_bytes,
                    "Visual PDF retained asset bytes exceed their bound.")
                identity = str(uuid5(_EMBEDDED_ASSET_NAMESPACE,
                    f"{source['id']}:{ordinal}:{row['content_sha256']}:{row['logical_name']}:{row['media_type']}"))
                _require(row["id"] == identity and row["media_type"].startswith("image/")
                    and row["created_at"] == row["retained_created_at"] == extraction["created_at"],
                    "Visual PDF retained asset ownership differs.")
        raw = rows[0]
        _require(raw["id"] == ids.raw_manifest_id and raw["content_sha256"] == source["raw_sha256"]
            and raw["size_bytes"] == source["raw_size_bytes"] and raw["media_type"] == source["media_type"]
            and raw["logical_name"] == source["original_filename"]
            and raw["created_at"] == raw["retained_created_at"] == source["created_at"],
            "Visual PDF raw manifest differs.")
        return rows

    def _snapshot(self,connection,preview_id,actor_user_id,*,now_ms):
        before = super()._snapshot(connection,preview_id,actor_user_id,now_ms=now_ms)
        return replace(before,fence_sha256=_hash(_json({"version":PDF_VISUAL_PREPARATION_VERSION,
            "retained_fence":before.fence_sha256})))

    def _verify_retained_asset(self,row,leases):
        observation,content = self._stager.verify_existing(expected_sha256=row["content_sha256"],
            expected_size=row["size_bytes"],media_type=row["media_type"],
            normalized_content_sha256=None,normalizer_version=None)
        lease,blob = getattr(observation,"lease",None),getattr(observation,"blob",None)
        if callable(getattr(lease,"close",None)): leases.append(lease)
        _require(type(content) is bytes and len(content) == row["size_bytes"]
            and sha256(content).hexdigest() == row["content_sha256"] and type(blob) is StagedVerifiedBlob
            and blob.sha256 == row["content_sha256"] and type(blob.size_bytes) is int
            and blob.size_bytes == row["size_bytes"] and blob.media_type == row["media_type"]
            and blob.relative_path == row["relative_path"]
            and type(blob.created_at_ms) is int and blob.created_at_ms == 0
            and type(blob.verified_at_ms) is int and blob.verified_at_ms == 0
            and callable(getattr(lease,"close",None)), "Visual PDF retained asset verifier evidence differs.")

    def prepare(self,*,preview_id,actor_user_id,chunk_bytes=8192,model_policy=None,
            work_policy=None,execution_policy=None,now_ms=0,cancel_event=None,policy_version=WRITING_VERSION,
            forward_partition=False):
        _require(type(forward_partition) is bool and (not forward_partition or policy_version == VERSION_V36),
            'Explicit ordinary original-range partition policy required.')
        if forward_partition:
            _require_atomic_partition_catalog(self._factory)
        prompt = prompt_for_policy(policy_version)  # Reject unknown policy before file I/O.
        if policy_version == VERSION_V36:
            _require_numeric_append_catalog(self._factory)
        if policy_version in (VERSION_V34, VERSION_V35, VERSION_V36):
            _require(self._chart_ocr_language is not None,
                "Chart preparation requires an explicit installed OCR language.")
        def cancelled():
            if cancel_event is not None and cancel_event.is_set(): raise PdfProcessError("CANCELLED")
        cancelled()
        before = self._read(preview_id,actor_user_id,now_ms=now_ms)
        _require(not forward_partition or before.source_extension is None,
            'Protected PDF sources retain their historical preparation policy.')
        # A new server may process an old queued registration. Select the old
        # input recipe when that registration did not opt into V14 admission.
        fresh_key_version = self._fresh_key_information_version
        if not forward_partition and fresh_key_version == LITERAL_CONTEXT_ADMITTED_OWNED_VERSION:
            fresh_key_version = LITERAL_CONTEXT_BOUNDED_OWNED_VERSION
        cancelled()  # A cancellation during authorization must not start CAS I/O.
        source = before.source
        leases,group = [],None
        try:
            loaded = self._loader.load(artifact_id=source["id"],expected_sha256=source["raw_sha256"],
                expected_size=source["raw_size_bytes"],expected_media_type=source["media_type"])
            leases.append(loaded.lease)
            _require(loaded.original_filename == source["original_filename"],"Visual PDF source filename changed.")
            for asset in before.assets:
                cancelled()
                self._verify_retained_asset(asset,leases)
            cancelled()
            _require(self._read(preview_id,actor_user_id,now_ms=now_ms).fence_sha256 == before.fence_sha256,
                "Visual PDF source changed during retained-asset verification.")
            protected_binding = None
            if before.source_extension is None:
                visual = prepare_pdf_visual_source(before.extraction["id"],loaded.original_filename,loaded.content,
                    python_executable=self._python,geometry_limits=self._limits,render_policy=self._render_policy,
                    process_policy=self._process_policy,cancel_event=cancel_event,
                    allow_visual_only=True,
                    allow_ambiguous_grid=(forward_partition and policy_version == VERSION_V36
                        and fresh_key_version == LITERAL_CONTEXT_ADMITTED_OWNED_VERSION
                        and self._allow_key_information_figures))
                _require(visual.geometry.snapshot_id == before.extraction["id"]
                    and visual.geometry.raw_sha256 == source["raw_sha256"]
                    and visual.geometry.raw_bytes == source["raw_size_bytes"]
                    and visual.geometry.filename == source["original_filename"],
                    "Visual PDF preparation differs from the retained original.")
            else:
                protected_binding = prepare_authorized_pdf_visual_source(
                    before.extraction["id"],loaded.original_filename,loaded.content,
                    owner_opener=self._protected_pdf_opener,python_executable=self._python,
                    geometry_limits=self._limits,render_policy=self._render_policy,
                    process_policy=self._process_policy,cancel_event=cancel_event)
                visual = protected_binding.check(original=loaded.content)
                coverage = json.loads(before.source_extension["coverage_json"])
                _require(protected_binding.original_sha256 == source["raw_sha256"]
                    and protected_binding.derivative_sha256 == before.source_extension["derivative_sha256"]
                    and protected_binding.expected_pages == before.source_extension["expected_locations"]
                    and protected_binding.visual_pages == tuple(row["ordinal"] for row in coverage
                        if row["visual_present"])
                    and visual.geometry.snapshot_id == before.extraction["id"],
                    "Protected PDF visual derivative differs from retained provenance.")
            layout_source = None
            figure_rois = None
            chart_supplement = None
            if policy_version in (VERSION_V33, VERSION_V34, VERSION_V35, VERSION_V36):
                parser_input = loaded.content if protected_binding is None else protected_binding.derivative_pdf
                cancelled()
                layout_source = prepare_layout(visual,parser_input,python_executable=self._python,
                    process_policy=self._process_policy,cancel_event=cancel_event)
                cancelled()
                if policy_version in (VERSION_V34, VERSION_V35, VERSION_V36):
                    # Initial preparation only. Cold jobs restore the exact
                    # retained observations rather than repeating OCR/fitting.
                    key_information = (policy_version == VERSION_V36 and protected_binding is None
                        and self._allow_key_information_figures)
                    chart_supplement = prepare_pdf_chart_supplement(layout_source, parser_input,
                        python_executable=self._python, language=self._chart_ocr_language,
                        limits=self._chart_limits, process_policy=self._process_policy,
                        cancel_event=cancel_event,
                        **({'allow_page_appearance_crops':not key_information, 'page_appearance_dpi':288,
                            'writer_input_version':None if key_information else CHART_WRITER_INPUT_VERSION}
                           if policy_version in (VERSION_V35, VERSION_V36) else {}),
                        **({'source_geometry_input_version':SOURCE_GEOMETRY_INPUT_VERSION,
                            'axis_fragment_recipe_version':AXIS_FRAGMENT_RECIPE_VERSION}
                           if policy_version == VERSION_V36 and protected_binding is None
                           and self._allow_thin_component_bands and not key_information else {}),
                        **({'literal_context_version':(fresh_key_version if key_information else
                            LITERAL_CONTEXT_NATIVE_VERSION if self._allow_native_text_presentation
                            else LITERAL_CONTEXT_TABLE_VERSION)}
                           if policy_version == VERSION_V36 and protected_binding is None
                           and self._allow_literal_definition_contexts else {}))
                    cancelled()
            if policy_version in (VERSION_V12,VERSION_V13,VERSION_V14,VERSION_V15, VERSION_V16, VERSION_V17, VERSION_V18, VERSION_V19, VERSION_V20, VERSION_V21, VERSION_V22, VERSION_V23, VERSION_V24, VERSION_V25, VERSION_V26, VERSION_V27, VERSION_V28, VERSION_V29, VERSION_V30, VERSION_V31, VERSION_V32):
                # Authorization, original CAS verification and the approved
                # protected derivative binding above precede both workers.
                # Never ask the layout/native workers to open encrypted raw.
                parser_input = loaded.content if protected_binding is None else protected_binding.derivative_pdf
                cancelled()
                native = prepare_native_figures(visual,parser_input,python_executable=self._python,
                    process_policy=self._process_policy,cancel_event=cancel_event)
                cancelled()
                layout_source = prepare_layout(visual,parser_input,python_executable=self._python,
                    process_policy=self._process_policy,cancel_event=cancel_event,native_figures=native)
                cancelled()
                if policy_version in (VERSION_V17, VERSION_V18, VERSION_V19, VERSION_V20, VERSION_V21, VERSION_V22, VERSION_V23, VERSION_V24, VERSION_V25, VERSION_V26, VERSION_V27, VERSION_V28, VERSION_V29, VERSION_V30, VERSION_V31, VERSION_V32):
                    figure_rois = prepare_figure_rois(layout_source,parser_input,python_executable=self._python,
                        process_policy=self._process_policy,cancel_event=cancel_event)
                    cancelled()
            effective_model = (
                (AuthoringModelPolicy(max_attempts=1,request_budget_bytes=1_114_112,max_tokens=4096)
                 if policy_version in (VERSION_V35, VERSION_V36) else AuthoringModelPolicy(max_attempts=1,request_budget_bytes=1_048_576))
                    if model_policy is None else model_policy)
            effective_work = AuthoringWorkPolicy() if work_policy is None else work_policy
            forward_source = (forward_partition and policy_version == VERSION_V36 and protected_binding is None
                and fresh_key_version == LITERAL_CONTEXT_ADMITTED_OWNED_VERSION
                and self._allow_key_information_figures)
            _require(not forward_partition or forward_source,
                'Original-range partition lacks its explicit forward input recipe.')
            if forward_source:
                # PNG/base64 transport bytes are not the 16k token context.
                # This new source binding retains the original context guard.
                effective_model = replace(effective_model,request_budget_bytes=4_194_304)
                # Whole-document transport reservation includes the durable
                # retry envelope; it is independent of each 16k model context.
                effective_work = replace(effective_work,logical_input_bytes=134_217_728)
            plan = PdfVisualPlan(visual,chunk_bytes,effective_model,
                effective_work,
                PreservationExecutionPolicy() if execution_policy is None else execution_policy,
                policy_version=policy_version,**({"layout_source":layout_source} if layout_source is not None else {}),
                **({"chart_supplement":chart_supplement} if policy_version in (VERSION_V34, VERSION_V35, VERSION_V36) else {}),
                **({'source_definitions':True} if policy_version == VERSION_V35 else {}),
                **({'source_definitions':False} if policy_version == VERSION_V36 else {}),
                **({'request_partition_policy':ATOMIC_REQUEST_PARTITION_V14} if forward_source else {}),
                **({"figure_rois":figure_rois} if policy_version in (VERSION_V17, VERSION_V18, VERSION_V19, VERSION_V20, VERSION_V21, VERSION_V22, VERSION_V23, VERSION_V24, VERSION_V25, VERSION_V26, VERSION_V27, VERSION_V28, VERSION_V29, VERSION_V30, VERSION_V31, VERSION_V32) else {}))
            cancelled()
            _require(self._read(preview_id,actor_user_id,now_ms=now_ms).fence_sha256 == before.fence_sha256,
                "Visual PDF source changed during preparation.")
            config = _json({"version":PDF_VISUAL_PREPARATION_VERSION,"policy_version":policy_version,
                "prompt_sha256":_hash(prompt),"chunk_bytes":chunk_bytes,"model_policy":asdict(plan.model_policy),
                "work_policy":asdict(plan.work_policy),"execution_policy":asdict(plan.execution_policy),
                "geometry_limits":asdict(self._limits),"process_policy":asdict(self._process_policy),
                "render_policy":asdict(visual.render_policy),"renderer_identity":renderer_identity(),
                "retained_asset_policy":asdict(self._asset_policy),
                **({'request_partition_policy':ATOMIC_REQUEST_PARTITION_V14} if forward_source else {}),
                **({"forward_source_policy":forward_source_descriptor(self._render_policy)} if forward_source else {}),
                **({"layout_identity":layout_source.identity()} if layout_source is not None else {}),
                **({"chart_identity":chart_supplement.identity(cancel_event=cancel_event),
                    "chart_preparation":{"version":CHART_PREPARATION_VERSION,
                        "language":self._chart_ocr_language,"limits":asdict(self._chart_limits)}}
                    if policy_version in (VERSION_V34, VERSION_V35, VERSION_V36) else {}),
                **({"figure_roi_identity":figure_roi_group_identity(layout_source,figure_rois)}
                    if policy_version in (VERSION_V17, VERSION_V18, VERSION_V19, VERSION_V20, VERSION_V21, VERSION_V22, VERSION_V23, VERSION_V24, VERSION_V25, VERSION_V26, VERSION_V27, VERSION_V28, VERSION_V29, VERSION_V30, VERSION_V31, VERSION_V32) else {}),
                **({"protected_binding":protected_binding.identity()} if protected_binding is not None else {})})
            retained = _json([{key:asset[key] for key in ("id","ordinal","logical_name","media_type","content_sha256","size_bytes")}
                for asset in before.assets])
            group = _Leases(self,leases)
            prepared = PreparedCanonicalPdfVisualSource(preview_id,actor_user_id,before.preview["durable_job_id"],
                source["id"],plan,before.fence_sha256,config,retained,"",group)
            result = replace(prepared,seal=_seal(prepared))
            cancelled()
            return result
        except BaseException:
            if group is not None:
                try: group.close()
                except BaseException: pass
            else:
                for lease in reversed(leases):
                    try: lease.close()
                    except BaseException: pass
            raise  # Attempt every release, retain the primary failure.

    def revalidate_in_transaction(self,connection,prepared,*,now_ms=0):
        """No parser, image decoder, CAS reads, model or transaction ownership."""
        _require(type(prepared) is PreparedCanonicalPdfVisualSource and prepared._leases.owner is self
            and prepared._leases.token in self._active,"Visual PDF source is foreign or closed.")
        try:
            valid = type(prepared.seal) is str and hmac.compare_digest(prepared.seal,_seal(prepared))
        except (ValueError,TypeError,AttributeError,UnicodeError,RecursionError): valid = False
        _require(valid,"Visual PDF preparation changed or belongs to another process.")
        current = self._snapshot(connection,prepared.preview_id,prepared.actor_user_id,now_ms=now_ms)
        _require(current.fence_sha256 == prepared.fence_sha256,"Visual PDF source changed before its effect.")
        return prepared

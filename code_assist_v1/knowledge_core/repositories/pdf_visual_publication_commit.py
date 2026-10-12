"""PDF-specific source binding inside the existing atomic typed publisher."""
from dataclasses import dataclass, replace
from hashlib import sha256
import hmac
import json

from .pdf_publication_commit import PdfPublicationCommitRepository
from .native_publication_commit import _metadata_fence
from .publication_body_request import has_pdf_visual_work, requested_publication_body
from .publication_commit import (
    FrozenPublicationSource, PublicationCommitRepository, _SourceBinding,
    PublicationCommitConflictError, PublicationCommitIntegrityError,
)
from .pdf_visual_authoring import PreparedVisualRun, PreparedVisualSnapshot, PdfVisualAuthoringRepository, PdfVisualStorageConflict
from ..services.pdf_visual_authoring_source import CanonicalPdfVisualSourceService
from ..services.pdf_authoring_source import PdfSourceConflict
from .pdf_visual_archive import PdfVisualArchiveRepository
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
from ..migrations.v086_pdf_visual_contract_alignment import POLICY_V27
from ..migrations.v085_pdf_curve_prompt_alignment import POLICY_V26
from ..migrations.v084_pdf_curve_named_bounds import POLICY_V25
from ..pdf_batch_markdown import VERSION as BATCH_MARKDOWN_VERSION
from ..pdf_source_faithful_markdown import VERSION as SOURCE_FAITHFUL_VERSION


def _manifest_processing_contract(manifest, policy_version):
    if policy_version == POLICY_V36:
        # Numeric observations are append-only; none enters the V10 writer.
        return ('source_definition_context' not in manifest
            and _manifest_processing_contract(manifest, POLICY_V34))
    if policy_version == POLICY_V35:
        from ..pdf_layout_term_context import SOURCE_DEFINITIONS_VERSION
        definitions = manifest.get('source_definition_context')
        if (type(definitions) is not dict or set(definitions) != {'version', 'semantic_definition_verification'}
                or definitions['version'] != SOURCE_DEFINITIONS_VERSION
                or definitions['semantic_definition_verification'] is not False):
            return False
        # The complete source/image relations are freshly replayed before
        # publication. No OCR or caption is promoted to semantic evidence.
        return _manifest_processing_contract(manifest, POLICY_V34)
    if policy_version == POLICY_V34:
        chart, literal = manifest.get('chart_readings'), manifest.get('literal_tables')
        if (type(chart) is not dict or set(chart) != {'identity','output_byte_range','rendered_sha256',
                'numerical_accuracy_verified','semantic_accuracy_verified','missing_values_filled'}
                or any(chart[key] is not False for key in ('numerical_accuracy_verified','semantic_accuracy_verified','missing_values_filled'))
                or type(literal) is not dict or type(chart.get('identity')) is not dict
                or chart['identity'].get('publication_authority') is not False):
            return False
        span = chart['output_byte_range']; terminal = literal.get('output_byte_range')
        return (type(span) is list and len(span) == 2 and all(type(n) is int for n in span)
            and 0 <= span[0] <= span[1] and type(terminal) is list and len(terminal) == 2
            and span[1] == terminal[0]-(1 if literal.get('table_count',0)>0 else 0)
            and _manifest_processing_contract(manifest, POLICY_V33))
    if policy_version == POLICY_V33:
        literal = manifest.get('literal_tables')
        return (type(literal) is dict and literal.get('version') == 'pdf-layout-literal-table-supplement-v1'
            and manifest['all_nonblank_ranges_delivered'] is True
            and manifest['all_nonblank_ranges_authored'] is False
            and manifest['all_visual_tasks_returned'] is False
            and manifest['provenance_level'] == 'batch_input_context'
            and all(manifest[key] is True for key in ('all_detected_grid_cells_rendered','all_pages_rendered')))
    if policy_version in (BATCH_MARKDOWN_VERSION,SOURCE_FAITHFUL_VERSION,POLICY_V12,POLICY_V13,POLICY_V14,POLICY_V15, POLICY_V16, POLICY_V17, POLICY_V18, POLICY_V19, POLICY_V20,POLICY_V21,POLICY_V22,POLICY_V23,POLICY_V24,POLICY_V25, POLICY_V26, POLICY_V27, POLICY_V28, POLICY_V29, POLICY_V30, POLICY_V31, POLICY_V32):
        return (manifest['all_nonblank_ranges_delivered'] is True
            and manifest['all_nonblank_ranges_authored'] is False
            and manifest['all_visual_tasks_returned'] is False
            and manifest['provenance_level']=='batch_input_context'
            and all(manifest[key] is True for key in ('all_detected_grid_cells_rendered','all_pages_rendered')))
    return all(manifest[key] is True for key in ('all_nonblank_ranges_authored',
        'all_detected_grid_cells_rendered','all_pages_rendered','all_visual_tasks_returned'))
from ..wiki_authoring import _hash


@dataclass(frozen=True, slots=True)
class FrozenPdfVisualPublicationSource(FrozenPublicationSource):
    pdf_visual_snapshot_id: str | None = None
    pdf_visual_manifest_json: str = ""
    pdf_visual_manifest_sha256: str = ""
    requested_fence: tuple = ()
    metadata_fence: str = ""
    verified_run: PreparedVisualRun | None = None
    body_seal: str = ""


class PdfVisualPublicationCommitRepository(PdfPublicationCommitRepository):
    def __init__(self, native_source, public_bodies, pdf_source, pdf_visual_source, visual_archive):
        super().__init__(native_source, public_bodies, pdf_source)
        if type(pdf_visual_source) is not CanonicalPdfVisualSourceService:
            raise TypeError("PDF publication needs its canonical source verifier.")
        if type(visual_archive) is not PdfVisualArchiveRepository:
            raise TypeError("Visual publication requires its archive verifier.")
        self.pdf_visual_source = pdf_visual_source
        self.visual_archive = visual_archive
        self.visual_runs = PdfVisualAuthoringRepository(pdf_visual_source, visual_archive)

    def _pdf_visual_seal(self, source):
        values = []
        for name in FrozenPdfVisualPublicationSource.__dataclass_fields__:
            if name == "body_seal":
                continue
            value = getattr(source, name)
            values.append((name, id(value) if name in ("content_md", "pdf_visual_manifest_json",
                "extraction_source", "verified_run") else value))
        return hmac.digest(self._preparation_key, repr(values).encode(), "sha256").hex()

    def _pdf_visual_requested(self, connection, binding):
        row = connection.execute("SELECT * FROM pdf_visual_publication_candidate WHERE preview_id=?",
            (binding.preview_id,)).fetchone()
        if (row is None or (row["actor_user_id"], row["artifact_id"], row["extraction_snapshot_id"],
                row["source_snapshot_sha256"]) != (binding.actor_user_id, binding.artifact_id,
                    binding.extraction_snapshot_id, binding.source_snapshot_sha256)):
            raise PublicationCommitConflictError("The PDF upload has no complete retained Wiki for publication.")
        identity = requested_publication_body(connection, actor_id=binding.actor_user_id, preview_id=binding.preview_id)
        if identity.kind != "pdf_visual_wiki" or identity.snapshot_id != row["snapshot_id"]:
            raise PublicationCommitIntegrityError("The requested PDF publication identity differs.")
        try:
            # SQL/CPU only. Parsing, retained-file leasing and receipt replay
            # are performed by the service before its writer transaction.
            current = self.pdf_visual_source._snapshot(connection, binding.preview_id,
                binding.actor_user_id, now_ms=binding.created_at_ms)
        except PdfSourceConflict as exc:
            raise PublicationCommitConflictError(str(exc)) from exc
        if current.fence_sha256 != row["source_fence_sha256"]:
            raise PublicationCommitConflictError("The PDF Wiki source changed.")
        return dict(row), tuple(row), identity, current

    @staticmethod
    def _manifest_source_bound(connection, requested, current, manifest):
        visual_source = manifest["source"]
        geometry = visual_source["geometry"]
        original = current.source
        if (geometry["filename"] != original["original_filename"]
                or type(geometry["raw_bytes"]) is not int):
            return False
        if manifest.get('version') in (POLICY_V34, POLICY_V35, POLICY_V36):
            row = connection.execute("""SELECT body.bundle_json FROM pdf_visual_authoring_run run
                JOIN pdf_visual_source_body body ON body.source_id=run.source_archive_id
                WHERE run.id=? AND run.preview_id=?""", (requested['run_id'],requested['preview_id'])).fetchone()
            if row is None:
                return False
            archive = json.loads(row[0]); config = json.loads(archive['configuration_json'])
            if (archive['source'] != visual_source or manifest['chart_readings']['identity'] != archive['chart_supplement']
                    or archive['chart_supplement'] != config['chart_identity']
                    or manifest['literal_tables']['source_identity'] != config['layout_identity']):
                return False
        protected = current.source_extension
        if protected is None:
            return (geometry["raw_sha256"] == original["raw_sha256"]
                and geometry["raw_bytes"] == original["raw_size_bytes"])
        row = connection.execute("""SELECT body.bundle_json FROM pdf_visual_authoring_run run
            JOIN pdf_visual_source_body body ON body.source_id=run.source_archive_id
            WHERE run.id=? AND run.preview_id=?""",
            (requested["run_id"], requested["preview_id"])).fetchone()
        if row is None:
            return False
        archive = json.loads(row[0])
        binding = json.loads(archive["configuration_json"])["protected_binding"]
        coverage = json.loads(protected["coverage_json"])
        visual_pages = [page["ordinal"] for page in coverage if page["visual_present"]]
        return (archive["source"] == visual_source
            and binding["derivative_visual_source"] == visual_source
            and binding["original_sha256"] == original["raw_sha256"]
            and binding["original_filename"] == original["original_filename"]
            and binding["derivative_sha256"] == protected["derivative_sha256"]
            and binding["opening_method"] == protected["opening_method"]
            and binding["expected_pages"] == protected["expected_locations"]
            and binding["visual_pages"] == visual_pages
            and geometry["raw_sha256"] == protected["derivative_sha256"]
            and geometry["raw_sha256"] != original["raw_sha256"]
            and geometry["raw_bytes"] > 0)

    def freeze_source(self, connection, **kwargs):
        if not has_pdf_visual_work(connection, kwargs["preview_id"]):
            return super().freeze_source(connection, **kwargs)
        if kwargs.get("authored_snapshot_id") is not None:
            raise PublicationCommitIntegrityError("A client snapshot cannot replace the PDF candidate.")
        binding = _SourceBinding(kwargs["actor_user_id"], kwargs["preview_id"], kwargs["artifact_id"],
            kwargs["extraction_snapshot_id"], kwargs["source_snapshot_sha256"], kwargs["effective_at_ms"])
        requested, fence, identity, current = self._pdf_visual_requested(connection, binding)
        source = PublicationCommitRepository.freeze_source(self, connection, **kwargs)
        row = connection.execute("SELECT content_md,manifest_json FROM pdf_visual_wiki_snapshot_body WHERE snapshot_id=?",
            (requested["snapshot_id"],)).fetchone()
        try:
            if row is None or any(type(value) is not str for value in row):
                raise ValueError
            content, manifest_json = row
            manifest = json.loads(manifest_json)
            if (sha256(content.encode()).hexdigest() != requested["content_sha256"]
                    or len(content.encode()) != requested["content_size_bytes"] or "\r" in content
                    or sha256(manifest_json.encode()).hexdigest() != requested["manifest_sha256"]
                    or len(manifest_json.encode()) != requested["manifest_size_bytes"]
                    or not self._manifest_source_bound(connection, requested, current, manifest)
                    or manifest["plan_sha256"] != requested["source_plan_sha256"]
                    or manifest["version"] != identity.policy_version
                    or (identity.policy_version, manifest["prompt_sha256"]) not in POLICY_IDENTITIES
                    or manifest["wiki_sha256"] != requested["content_sha256"]
                    or type(manifest["wiki_bytes"]) is not int or manifest["wiki_bytes"] != requested["content_size_bytes"]
                    or not _manifest_processing_contract(manifest,identity.policy_version)
                    or any(manifest[key] is not False for key in ("semantic_review_performed",
                        "semantic_completeness_proven", "caption_ownership_inferred", "visual_accuracy_verified", "canonical_publication_ready"))):
                raise ValueError
        except (KeyError, TypeError, ValueError, UnicodeError, RecursionError) as exc:
            raise PublicationCommitIntegrityError("The PDF Wiki body and manifest are inconsistent.") from exc
        fields = {name: getattr(source, name) for name in FrozenPublicationSource.__dataclass_fields__}
        fields.update(raw_sha256=requested["content_sha256"], normalized_sha256=requested["content_sha256"],
            size_bytes=requested["content_size_bytes"], normalizer_version="markdown_lf_v1", content_md=content,
            content_blob_hash=None, content_sha256=requested["content_sha256"], extraction_source=source,
            reuse_targets=kwargs.get("authorized_targets", ()), reuse_fence=())
        prepared = FrozenPdfVisualPublicationSource(**fields, pdf_visual_snapshot_id=requested["snapshot_id"],
            pdf_visual_manifest_json=manifest_json, pdf_visual_manifest_sha256=requested["manifest_sha256"],
            requested_fence=fence, metadata_fence=_metadata_fence(connection, binding))
        return replace(prepared, body_seal=self._pdf_visual_seal(prepared))

    def bind_replayed_snapshot(self, source, run, snapshot):
        """The owning service reconstructs/assembles outside every SQLite txn."""
        if type(source) is not FrozenPdfVisualPublicationSource:
            return super().bind_replayed_snapshot(source, run, snapshot)
        if (type(source) is not FrozenPdfVisualPublicationSource or type(run) is not PreparedVisualRun
                or type(snapshot) is not PreparedVisualSnapshot
                or not hmac.compare_digest(source.body_seal, self._pdf_visual_seal(source))
                or source.pdf_visual_snapshot_id != snapshot.snapshot_id or snapshot.run_id != run.run_id
                or source.content_md != snapshot.markdown or source.pdf_visual_manifest_json != snapshot.manifest_json
                or snapshot.run_metadata_sha256 != _hash(run.metadata_json)):
            raise PublicationCommitIntegrityError("The PDF publication differs from its replayed writer receipts.")
        source = replace(source, verified_run=run, body_seal="")
        return replace(source, body_seal=self._pdf_visual_seal(source))

    def _revalidate_own_candidate(self, connection, draft, source):
        if type(source) is not FrozenPdfVisualPublicationSource:
            return super()._revalidate_own_candidate(connection, draft, source)
        requested, fence, _, current = self._pdf_visual_requested(connection, draft)
        run = source.verified_run
        if (source.requested_fence != fence or source.metadata_fence != _metadata_fence(connection, draft)
                or source.pdf_visual_snapshot_id != requested["snapshot_id"]
                or source.pdf_visual_manifest_sha256 != requested["manifest_sha256"]
                or type(run) is not PreparedVisualRun or run.run_id != requested["run_id"]
                or run.source.fence_sha256 != current.fence_sha256
                or run.source._leases.token not in self.pdf_visual_source._active
                or source.extraction_source is None or source.reuse_targets != draft.targets
                or source.authored_snapshot_id is not None or source.reused_extraction_snapshot_id is not None
                or source.authored_preparation is not None or source.content_blob_hash is not None
                or source.reuse_fence != () or type(source.content_md) is not str
                or (source.raw_sha256, source.normalized_sha256, source.content_sha256, source.size_bytes,
                    source.normalizer_version) != (requested["content_sha256"], requested["content_sha256"],
                    requested["content_sha256"], requested["content_size_bytes"], "markdown_lf_v1")
                or type(source.body_seal) is not str or not hmac.compare_digest(source.body_seal, self._pdf_visual_seal(source))):
            raise PublicationCommitConflictError("The prepared PDF publication is stale or inconsistent.")
        try:
            # The restored plan includes mutable internal caches. Object ID,
            # persisted plan hash and an open lease do not validate those bytes.
            # This deep source seal is SQL/CPU-only, with no parser or file I/O.
            self.visual_runs._bound(connection, run)
        except (PdfSourceConflict, PdfVisualStorageConflict) as exc:
            raise PublicationCommitConflictError(str(exc)) from exc
        PublicationCommitRepository._revalidate_frozen_source(connection, draft, source.extraction_source)

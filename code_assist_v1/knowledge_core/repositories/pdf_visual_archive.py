"""Exact visual source retention, not a model run or publication service.

Preparation/read CAS I/O is outside SQLite. put/load are caller-transaction
primitives; a future workflow service owns audit/outbox and model attempt fences.
No production route calls this opt-in repository yet.
"""
from dataclasses import asdict, dataclass, field, replace
from hashlib import sha256
import hmac
import json
import re
import secrets
from uuid import UUID, uuid5

from ..db import transaction, TransactionMode, db_now_ms
from ..migrations.v042_pdf_visual_source_storage import POLICY
from ..wiki_authoring import _json, _hash
from ..services.pdf_visual_authoring_source import PreparedCanonicalPdfVisualSource, _seal as _source_seal, _require_numeric_append_catalog
from ..services.native_authoring_source import _Leases
from ..pdf_visual_authoring import pdf_visual_request, VERSION_V34, VERSION_V35, VERSION_V36
from ..pdf_chart_supplement import PdfChartSupplement, VERSION as CHART_VERSION, MAXIMUM_INPUT_BYTES as CHART_BYTES
from ..pdf_visual_source import PdfRenderedPage
from ...llm_images import ModelImageInput, ModelImagePolicy, validate_image_inputs
from ...document_pdf_process import PdfProcessError
from .artifacts import ArtifactRepository, StagedVerifiedBlob, CAS_BLOB_MEDIA_TYPE, _connection

_KEY = secrets.token_bytes(32)
_NS = UUID("b283f75e-537f-4f62-9cde-96c1b23c0978")
_FIELDS = ("id","operation_id","preview_id","actor_user_id","artifact_id","extraction_snapshot_id",
    "parent_job_id","policy_version","source_fence_sha256","plan_sha256","bundle_sha256","bundle_size_bytes",
    "geometry_sha256","geometry_size_bytes","page_count","page_bytes","page_pixels")
_PAGE_FIELDS = ("page","image_id","width","height","content_sha256","size_bytes")
_BUNDLE_FIELDS = {"version","source","plan_sha256","configuration_json","retained_assets_json","requests"}
_CHART_FIELDS = {'version','sha256','bytes','source_geometry_sha256','source_raw_sha256',
    'layout_catalog_sha256','figure_count','publication_authority'}
_SHA = re.compile(r'[0-9a-f]{64}\Z')


class PdfVisualArchiveConflict(ValueError): pass


def _require(value,message="Retained PDF visual source differs or is unavailable."):
    if not value: raise PdfVisualArchiveConflict(message)


def _operation(value):
    try: valid = type(value) is str and str(UUID(value))==value
    except (ValueError,AttributeError): valid=False
    _require(valid,"A canonical operation UUID is required.")
    return value


def _mac(kind,value): return hmac.digest(_KEY,(kind+"\0"+_json(value)).encode(),"sha256").hex()


def _cancel(event):
    if event is not None and event.is_set(): raise PdfProcessError("CANCELLED")


def _close_all(leases):
    for lease in reversed(leases):
        try: lease.close()
        except BaseException: pass


def _chart_descriptor(bundle):
    """Only the forward policy has one compact, source-bound CAS descriptor."""
    try:
        configuration = json.loads(bundle['configuration_json'])
        if configuration['policy_version'] not in (VERSION_V34, VERSION_V35, VERSION_V36):
            _require(set(bundle) == _BUNDLE_FIELDS)
            return None
        _require(set(bundle) == _BUNDLE_FIELDS | {'chart_supplement'})
        chart = bundle['chart_supplement']
        _require(type(chart) is dict and set(chart) == _CHART_FIELDS
            and chart['version'] == CHART_VERSION and chart['publication_authority'] is False
            and type(chart['bytes']) is int and 1 <= chart['bytes'] <= CHART_BYTES
            and type(chart['figure_count']) is int and 0 <= chart['figure_count'] <= 4096
            and all(type(chart[key]) is str and _SHA.fullmatch(chart[key]) is not None for key in
                ('sha256','source_geometry_sha256','source_raw_sha256','layout_catalog_sha256'))
            and _json(chart) == _json(configuration['chart_identity'])
            and chart['source_geometry_sha256'] == bundle['source']['geometry']['geometry_sha256']
            and chart['source_raw_sha256'] == bundle['source']['geometry']['raw_sha256']
            and chart['layout_catalog_sha256'] == configuration['layout_identity']['catalog_sha256'],
            'The retained chart descriptor differs from its source or configuration.')
        return chart
    except PdfVisualArchiveConflict:
        raise
    except (KeyError, TypeError, ValueError, UnicodeError, RecursionError) as error:
        raise PdfVisualArchiveConflict('The retained chart descriptor is unavailable or incompatible.') from error


@dataclass(frozen=True,slots=True)
class PreparedPdfVisualArchive:
    source: PreparedCanonicalPdfVisualSource = field(repr=False)
    metadata: tuple
    bundle_json: str = field(repr=False)
    pages: tuple[tuple,...]
    blobs: tuple[StagedVerifiedBlob,...] = field(repr=False)
    seal: str = field(repr=False)
    _leases: _Leases = field(repr=False,compare=False)

    def close(self): self._leases.close()  # Caller continues to own original source.
    def __enter__(self):
        _require(self._leases.token in self._leases.owner._active); return self
    def __exit__(self,*_): self.close()


def _seal(p):
    return _mac("archive",[p.source.seal,p.metadata,p.bundle_json,p.pages,
        [asdict(b) for b in p.blobs],p._leases.token])


@dataclass(frozen=True,slots=True)
class ArchivedPdfVisualBytes:
    metadata: tuple
    bundle_json: str = field(repr=False)
    geometry: bytes = field(repr=False)
    pages: tuple[PdfRenderedPage,...] = field(repr=False)
    _leases: _Leases = field(repr=False,compare=False)
    chart_supplement: bytes | None = field(default=None, repr=False, kw_only=True)

    def close(self): self._leases.close()
    def __enter__(self):
        _require(self._leases.token in self._leases.owner._active); return self
    def __exit__(self,*_): self.close()


class PdfVisualArchiveRepository:
    def __init__(self,source_service,stager):
        self.source_service,self.stager = source_service,stager
        self._active = set()

    def _source_current(self,source):
        _require(type(source) is PreparedCanonicalPdfVisualSource
            and source._leases.owner is self.source_service
            and source._leases.token in self.source_service._active,
            "The original PDF preparation is foreign or closed.")
        _require(type(source.seal) is str and hmac.compare_digest(source.seal,_source_seal(source)))
        if source.plan.policy_version == VERSION_V36:
            _require_numeric_append_catalog(self.source_service._factory)
        c=self.source_service._factory.open(read_only=True)
        try:
            with transaction(c,TransactionMode.DEFERRED):
                current=self.source_service._snapshot(c,source.preview_id,source.actor_user_id,now_ms=0)
                _require(current.fence_sha256==source.fence_sha256)
                return db_now_ms(c)
        finally: c.close()

    def _verify_observation(self,observation,content,media_type,leases,*,expected_time=0):
        lease,blob = getattr(observation,"lease",None),getattr(observation,"blob",None)
        if callable(getattr(lease,"close",None)): leases.append(lease)
        _require(type(content) is bytes,"CAS content must be exact bytes.")
        digest = sha256(content).hexdigest()
        _require(type(content) is bytes and content and type(blob) is StagedVerifiedBlob
            and blob.sha256==digest and type(blob.size_bytes) is int and blob.size_bytes==len(content)
            and blob.media_type==media_type and blob.relative_path==f"{digest[:2]}/{digest[2:4]}/{digest}"
            and type(blob.created_at_ms) is int and blob.created_at_ms==expected_time
            and type(blob.verified_at_ms) is int and blob.verified_at_ms==expected_time
            and callable(getattr(lease,"close",None)),"CAS staging evidence differs from the visual source.")
        return blob

    def prepare(self,source,*,operation_id,cancel_event=None):
        _operation(operation_id); _cancel(cancel_event)
        staged_at_ms=self._source_current(source)
        p = source.plan
        value = {"version":POLICY,"source":p.source.identity(),"plan_sha256":p.plan_sha256,
            "configuration_json":source.configuration_json,"retained_assets_json":source.retained_assets_json,
            "requests":[pdf_visual_request(p,b.ordinal)[2] for b in p.batches]}
        chart_content = None
        if p.policy_version in (VERSION_V34, VERSION_V35, VERSION_V36):
            _require(type(p.chart_supplement) is PdfChartSupplement)
            value['chart_supplement'] = p.chart_supplement.identity(cancel_event=cancel_event)
            chart_content = p.chart_supplement.metadata_json.encode('utf-8')
        _chart_descriptor(value)
        bundle = _json(value)
        _require(0<len(bundle.encode())<=1_048_576)
        pages = tuple((page.page,page.image.source_id,page.width,page.height,
            sha256(page.image.data).hexdigest(),len(page.image.data)) for page in p.source.pages)
        geometry = p.source.geometry.geometry_json
        metadata = (str(uuid5(_NS,source.preview_id)),operation_id,source.preview_id,source.actor_user_id,
            source.artifact_id,p.source.geometry.snapshot_id,source.parent_job_id,POLICY,source.fence_sha256,
            p.plan_sha256,_hash(bundle),len(bundle.encode()),sha256(geometry).hexdigest(),len(geometry),len(pages),
            sum(row[5] for row in pages),sum(row[2]*row[3] for row in pages))
        leases,group = [],None
        try:
            blobs = []
            contents = ((geometry,"application/json"),*((page.image.data,"image/png") for page in p.source.pages))
            if chart_content is not None:
                contents += ((chart_content,'application/json'),)
            for content,mime in contents:
                _cancel(cancel_event)
                observation = self.stager.stage_and_verify(content,expected_sha256=sha256(content).hexdigest(),
                    media_type=mime,effective_at_ms=staged_at_ms,normalized_content_sha256=None,normalizer_version=None)
                blobs.append(self._verify_observation(observation,content,mime,leases,expected_time=staged_at_ms))
            _cancel(cancel_event); self._source_current(source); _cancel(cancel_event)
            group = _Leases(self,leases)
            prepared = PreparedPdfVisualArchive(source,metadata,bundle,pages,tuple(blobs),"",group)
            return replace(prepared,seal=_seal(prepared))
        except BaseException:
            if group is None: _close_all(leases)
            else:
                try: group.close()
                except BaseException: pass
            raise

    def put(self,connection,prepared):
        """Caller owns complete transaction and audit/outbox, no I/O/model here."""
        _connection(connection,write=True)
        _require(type(prepared) is PreparedPdfVisualArchive and prepared._leases.owner is self
            and prepared._leases.token in self._active)
        _require(type(prepared.seal) is str and hmac.compare_digest(prepared.seal,_seal(prepared)))
        self.source_service.revalidate_in_transaction(connection,prepared.source)
        p = prepared
        prior = connection.execute("SELECT * FROM pdf_visual_source WHERE id=? OR operation_id=? OR preview_id=?",
            (p.metadata[0],p.metadata[1],p.metadata[2])).fetchall()
        if prior:
            _require(len(prior)==1 and tuple(prior[0][f] for f in _FIELDS)==p.metadata,
                "Visual archive operation or preview is already bound differently.")
            metadata,bundle,pages = self.load(connection,preview_id=p.metadata[2],actor_user_id=p.metadata[3])
            _require((metadata,bundle,pages)==(p.metadata,p.bundle_json,p.pages))
            return p.metadata[0]
        unique = {b.sha256:b for b in p.blobs}
        new_blobs = tuple(b for b in unique.values() if ArtifactRepository._preflight_blob(connection,b))
        for b in new_blobs:
            connection.execute("""INSERT INTO blob_object(sha256,size_bytes,media_type,relative_path,verification_status,
                created_at,verified_at) VALUES(?,?,?,?,'verified',?,?)""",
                (b.sha256,b.size_bytes,CAS_BLOB_MEDIA_TYPE,b.relative_path,b.created_at_ms,b.verified_at_ms))
        connection.execute("INSERT INTO pdf_visual_source("+",".join(_FIELDS)+",created_at) VALUES("+
            ",".join("?" for _ in range(len(_FIELDS)+1))+")",(*p.metadata,db_now_ms(connection)))
        connection.execute("INSERT INTO pdf_visual_source_body(source_id,bundle_json,geometry_content_sha256) VALUES(?,?,?)",
            (p.metadata[0],p.bundle_json,p.blobs[0].sha256))
        for row in p.pages:
            connection.execute("INSERT INTO pdf_visual_page(source_id,"+",".join(_PAGE_FIELDS)+") VALUES(?,?,?,?,?,?,?)",
                (p.metadata[0],*row))
            connection.execute("INSERT INTO pdf_visual_page_body(source_id,page,content_sha256) VALUES(?,?,?)",
                (p.metadata[0],row[0],row[4]))
        connection.execute("INSERT INTO pdf_visual_source_ready(source_id) VALUES(?)",(p.metadata[0],))
        _require(self.load(connection,preview_id=p.metadata[2],actor_user_id=p.metadata[3])==
            (p.metadata,p.bundle_json,p.pages))
        return p.metadata[0]

    def load(self,connection,*,preview_id,actor_user_id):
        """Authorized private settings/descriptors under an explicit snapshot."""
        _connection(connection,write=True)
        current = self.source_service._snapshot(connection,preview_id,actor_user_id,now_ms=0)
        row = connection.execute("""SELECT s.*,b.bundle_json,b.geometry_content_sha256 FROM pdf_visual_source s
            JOIN pdf_visual_source_ready r ON r.source_id=s.id
            JOIN pdf_visual_source_body b ON b.source_id=s.id WHERE s.preview_id=?""",(preview_id,)).fetchone()
        _require(row is not None and row["actor_user_id"]==actor_user_id
            and row["source_fence_sha256"]==current.fence_sha256)
        bundle = row["bundle_json"]
        _require(type(bundle) is str and len(bundle.encode())==row["bundle_size_bytes"]
            and _hash(bundle)==row["bundle_sha256"] and row["geometry_content_sha256"]==row["geometry_sha256"])
        pages = tuple(tuple(r[f] for f in _PAGE_FIELDS) for r in connection.execute(
            """SELECT p.* FROM pdf_visual_page p JOIN pdf_visual_page_body b
            ON b.source_id=p.source_id AND b.page=p.page AND b.content_sha256=p.content_sha256
            WHERE p.source_id=? ORDER BY p.page""",(row["id"],)))
        value = json.loads(bundle)
        if json.loads(value['configuration_json'])['policy_version'] == VERSION_V36:
            _require_numeric_append_catalog(self.source_service._factory)
        _chart_descriptor(value)
        _require(value["version"]==POLICY and value["plan_sha256"]==row["plan_sha256"]
            and len(pages)==row["page_count"] and [r[0] for r in pages]==list(range(1,len(pages)+1))
            and sum(r[5] for r in pages)==row["page_bytes"] and sum(r[2]*r[3] for r in pages)==row["page_pixels"])
        expected = tuple((p["page"],p["source_id"],p["width"],p["height"],p["sha256"],p["bytes"])
            for p in value["source"]["pages"])
        _require(expected==pages and value["source"]["geometry"]["geometry_sha256"]==row["geometry_sha256"]
            and value["source"]["geometry"]["geometry_bytes"]==row["geometry_size_bytes"])
        return tuple(row[f] for f in _FIELDS),bundle,pages

    def _load(self,preview_id,actor_user_id):
        c = self.source_service._factory.open(read_only=True)
        try:
            with transaction(c,TransactionMode.DEFERRED):
                return self.load(c,preview_id=preview_id,actor_user_id=actor_user_id)
        finally: c.close()

    def read(self,*,preview_id,actor_user_id,cancel_event=None):
        """Exact retained bytes; NOT a writer-ready canonical preparation yet."""
        _cancel(cancel_event)
        saved = self._load(preview_id,actor_user_id)
        metadata,bundle,pages = saved
        chart = _chart_descriptor(json.loads(bundle))
        leases,group = [],None
        try:
            contents = []
            for digest,size,mime in ((metadata[12],metadata[13],"application/json"),
                    *((p[4],p[5],"image/png") for p in pages)):
                _cancel(cancel_event)
                observed,content = self.stager.verify_existing(expected_sha256=digest,expected_size=size,media_type=mime,
                    normalized_content_sha256=None,normalizer_version=None)
                blob = self._verify_observation(observed,content,mime,leases)
                _require(blob.sha256==digest and blob.size_bytes==size)
                contents.append(content)
            chart_content = None
            if chart is not None:
                _cancel(cancel_event)
                observed, chart_content = self.stager.verify_existing(expected_sha256=chart['sha256'],
                    expected_size=chart['bytes'], media_type='application/json',
                    normalized_content_sha256=None, normalizer_version=None)
                blob = self._verify_observation(observed,chart_content,'application/json',leases)
                _require(blob.sha256==chart['sha256'] and blob.size_bytes==chart['bytes'])
            images = tuple(ModelImageInput(p[1],"image/png",content) for p,content in zip(pages,contents[1:],strict=True))
            info = validate_image_inputs(images,ModelImagePolicy(len(images),16_777_216,67_108_864,16_000_000,256_000_000))
            _require(all((p[2],p[3])==(v["width"],v["height"]) for p,v in zip(pages,info,strict=True)))
            _cancel(cancel_event); _require(self._load(preview_id,actor_user_id)==saved); _cancel(cancel_event)
            group = _Leases(self,leases)
            return ArchivedPdfVisualBytes(metadata,bundle,contents[0],
                tuple(PdfRenderedPage(p[0],p[2],p[3],im) for p,im in zip(pages,images,strict=True)),group,
                chart_supplement=chart_content)
        except BaseException:
            if group is None: _close_all(leases)
            else:
                try: group.close()
                except BaseException: pass
            raise

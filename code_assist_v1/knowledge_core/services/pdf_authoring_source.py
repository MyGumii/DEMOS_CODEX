"""Authorized retained-PDF preparation, not a publication endpoint.

Read/authorize -> close SQLite snapshot -> lease and verify retained original ->
isolated PDF parser and complete writer plan -> reauthorize. A future durable
producer must keep the lease open and revalidate immediately before its effect.
No model, enqueue, DB mutation, source rewriting or rollout is performed here.
The legacy extraction binds the ready upload lifecycle; its text is NEVER the
new writer's source. New geometry is reconstructed from the verified PDF bytes.
"""
from dataclasses import asdict, dataclass, field, fields, is_dataclass, replace
from hashlib import sha256
import hmac
import json
from pathlib import PurePath
import secrets
from uuid import UUID

from ...document_pdf_geometry import PdfGeometryLimits
from ...document_pdf_process import PdfProcessPolicy
from ..db import ManagedConnection, TransactionMode, transaction
from ..pdf_geometry_authoring import PdfAuthoringPlan, prepare_pdf_source, PDF_PROMPT
from ..repositories.publication import PublicationPreviewRepository
from ..repositories.wiki_preservation import WikiPreservationRepository
from ..wiki_authoring import AuthoringModelPolicy, AuthoringWorkPolicy, _hash, _json
from ..wiki_preservation import PreservationExecutionPolicy
from ..worker import DurableWorkKernel, HandlerRegistry
from .native_authoring_source import _Leases
from .publication import (
    CanonicalPublicationSourceLoader, PublicationPreviewExtractionHandler,
    PUBLICATION_PREVIEW_RULE_VERSION, publication_artifact_ids, publication_preview_id,
    Utf8PublicationExtractor,
)

PDF_SOURCE_PREPARATION_VERSION = "canonical-pdf-authoring-source-v1"
_KEY = secrets.token_bytes(32)
_MAX_NODES = 1_000_000
_MAX_TEXT_BYTES = 64 * 1024 * 1024
_LEGACY_EXTRACTOR = ("canonical-pypdf", "rich-source-v3-bounded-tables")


class PdfSourceConflict(ValueError):
    """PDF source/authority unavailable or changed; no private source text."""


def _require(value, message):
    if not value:
        raise PdfSourceConflict(message)


@dataclass(frozen=True, slots=True)
class PreparedCanonicalPdfSource:
    preview_id: str
    actor_user_id: str
    parent_job_id: str
    artifact_id: str
    plan: PdfAuthoringPlan = field(repr=False)
    fence_sha256: str
    configuration_json: str
    seal: str = field(repr=False)
    _leases: _Leases = field(repr=False, compare=False)
    source_preparation_version: str = PDF_SOURCE_PREPARATION_VERSION

    def close(self):
        self._leases.close()

    def __enter__(self):
        _require(self._leases.token in self._leases.owner._active, "PDF source preparation is closed.")
        return self

    def __exit__(self, *_):
        self.close()


@dataclass(frozen=True, slots=True)
class _Snapshot:
    preview: dict
    source: dict
    extraction: dict
    fence_sha256: str
    assets: tuple[dict, ...] = ()
    source_extension: dict | None = None


def _plan_digest(plan):
    return _typed_plan_digest(plan, PDF_PROMPT, expected_type=PdfAuthoringPlan,
        maximum_hashed_bytes=_MAX_TEXT_BYTES)


def _typed_plan_digest(plan, prompt, *, expected_type, maximum_hashed_bytes,
        deduplicate=False, maximum_text_bytes=_MAX_TEXT_BYTES):
    """Bounded typed hash of actual fields/caches, with no parser or file I/O.

    A retained plan ID alone is not authority for mutable Python caches. This
    seal is not a security boundary against code executing in this process.
    """
    _require(type(plan) is expected_type, "PDF preparation plan changed.")
    _require(type(deduplicate) is bool,"PDF preparation seal traversal policy is invalid.")
    if deduplicate:
        return _deduplicated_typed_plan_digest(plan,prompt,maximum_hashed_bytes=maximum_hashed_bytes,
            maximum_text_bytes=maximum_text_bytes)
    nodes = text_bytes = hashed_bytes = 0
    # Only immutable byte hashes are shared within this traversal. Retain each
    # object so its identity cannot be reused; all occurrence bounds stay fresh.
    byte_digests = {}

    def walk(value, depth=0):
        nonlocal nodes, text_bytes, hashed_bytes
        nodes += 1
        _require(nodes <= _MAX_NODES and depth <= 64, "PDF preparation seal exceeds its bound.")
        if value is None:
            return ["none"]
        if type(value) is bool:
            return ["bool", value]
        if type(value) is int:
            return ["int", value]
        if type(value) is float:
            return ["float", value]  # _json rejects non-finite coordinates/time.
        if type(value) is str:
            text_bytes += len(value.encode("utf-8"))
            _require(text_bytes <= _MAX_TEXT_BYTES, "PDF preparation text exceeds its seal bound.")
            return ["str", value]
        if type(value) is bytes:
            hashed_bytes += len(value)
            _require(hashed_bytes <= maximum_hashed_bytes, "PDF preparation byte inputs exceed their seal bound.")
            identity = id(value)
            cached = byte_digests.get(identity)
            if cached is None:
                cached = (value, sha256(value).hexdigest())
                byte_digests[identity] = cached
            return ["bytes", len(value), cached[1]]
        if type(value) in (tuple, list):
            _require(len(value) <= _MAX_NODES - nodes, "PDF preparation container exceeds its bound.")
            return [type(value).__name__, [walk(v, depth + 1) for v in value]]
        if type(value) is dict:
            _require(len(value) * 2 <= _MAX_NODES - nodes, "PDF preparation map exceeds its bound.")
            rows = [(walk(k, depth + 1), walk(v, depth + 1)) for k, v in value.items()]
            return ["dict", sorted(rows, key=lambda row: _json(row[0]))]
        if is_dataclass(value) and not isinstance(value, type):
            return [type(value).__module__ + "." + type(value).__qualname__,
                [[f.name, walk(getattr(value, f.name), depth + 1)] for f in fields(value)]]
        raise PdfSourceConflict("PDF preparation contains an unsupported value.")

    return _hash(_json({"plan": walk(plan), "prompt": prompt}))


def _deduplicated_typed_plan_digest(plan,prompt,*,maximum_hashed_bytes,maximum_text_bytes):
    """V12-only bounded Merkle seal over actual fields and mutable caches.

    The memo belongs to this one invocation. Revalidation rereads every unique
    object, never trusts a prior plan ID/cache, and performs no worker/image/I/O
    calls. Shared page/source bytes are hashed once, while every containing
    field and typed edge still contributes to its parent's digest. Historical
    policies retain their original traversal and digest above.
    """
    _require(type(maximum_hashed_bytes) is int and maximum_hashed_bytes>0
        and type(maximum_text_bytes) is int and maximum_text_bytes>0,
        "PDF preparation seal byte policies are invalid.")
    nodes=text_bytes=hashed_bytes=0
    memo,active={},set()
    def walk(value,depth=0):
        nonlocal nodes,text_bytes,hashed_bytes
        nodes+=1
        _require(nodes<=_MAX_NODES and depth<=64,"PDF preparation seal exceeds its bound.")
        identity=id(value)
        _require(identity not in active,"PDF preparation seal contains a cycle.")
        if identity in memo:
            digest,height=memo[identity]
            _require(depth+height<=64,"PDF preparation seal exceeds its bound.")
            return digest,height
        active.add(identity)
        height=0
        def child(item):
            nonlocal height
            digest,child_height=walk(item,depth+1)
            height=max(height,child_height+1)
            return digest
        try:
            if value is None:
                node=["none"]
            elif type(value) in (bool,int,float):
                node=[type(value).__name__,value]
            elif type(value) is str:
                encoded=value.encode('utf-8')
                text_bytes+=len(encoded)
                _require(text_bytes<=maximum_text_bytes,"PDF preparation text exceeds its seal bound.")
                node=["str",len(encoded),sha256(encoded).hexdigest()]
            elif type(value) is bytes:
                hashed_bytes+=len(value)
                _require(hashed_bytes<=maximum_hashed_bytes,"PDF preparation byte inputs exceed their seal bound.")
                node=["bytes",len(value),sha256(value).hexdigest()]
            elif type(value) in (tuple,list):
                _require(len(value)<=_MAX_NODES-nodes,"PDF preparation container exceeds its bound.")
                node=[type(value).__name__,[child(v) for v in value]]
            elif type(value) is dict:
                _require(len(value)*2<=_MAX_NODES-nodes,"PDF preparation map exceeds its bound.")
                node=["dict",sorted((child(k),child(v)) for k,v in value.items())]
            elif is_dataclass(value) and not isinstance(value,type):
                node=[type(value).__module__+"."+type(value).__qualname__,
                    [[f.name,child(getattr(value,f.name))] for f in fields(value)]]
            else:
                raise PdfSourceConflict("PDF preparation contains an unsupported value.")
            _require(depth+height<=64,"PDF preparation seal exceeds its bound.")
            result=(_hash(_json(node)),height)
            memo[identity]=result
            return result
        finally:
            active.remove(identity)
    return _hash(_json({"version":"bounded-typed-plan-v1","plan":walk(plan)[0],"prompt":walk(prompt)[0]}))


def _seal(prepared):
    _require(type(prepared) is PreparedCanonicalPdfSource
        and prepared.source_preparation_version == PDF_SOURCE_PREPARATION_VERSION,
        "PDF source preparation policy changed.")
    record = {"version": prepared.source_preparation_version, "preview_id": prepared.preview_id,
        "actor_user_id": prepared.actor_user_id, "parent_job_id": prepared.parent_job_id,
        "artifact_id": prepared.artifact_id, "fence_sha256": prepared.fence_sha256,
        "configuration_json": prepared.configuration_json, "private_plan_sha256": _plan_digest(prepared.plan),
        "lease_token": prepared._leases.token}
    return hmac.digest(_KEY, _json(record).encode(), "sha256").hex()


class CanonicalPdfAuthoringSourceService:
    def __init__(self, factory, scope_resolver, stager, *, python_executable,
                 geometry_limits=PdfGeometryLimits(), process_policy=PdfProcessPolicy()):
        _require(type(geometry_limits) is PdfGeometryLimits and type(process_policy) is PdfProcessPolicy,
            "Explicit PDF geometry and process policies are required.")
        self._factory, self._stager = factory, stager
        self._python = python_executable
        self._limits, self._process_policy = geometry_limits, process_policy
        self._loader = CanonicalPublicationSourceLoader(factory, stager)
        # Registry is used only to revalidate the completed parent's authority.
        # No extraction handler is executed by this source service.
        parent = PublicationPreviewExtractionHandler(factory, self._loader, Utf8PublicationExtractor())
        self._authority = DurableWorkKernel(factory, HandlerRegistry((parent.registered_handler(),)),
            scope_resolver=scope_resolver)
        self._active = set()

    def _source_extension(self, connection, source, extraction):
        """Keep the original PDF source contract exact; successors may extend it."""
        _require((extraction["extractor_name"], extraction["extractor_version"]) == _LEGACY_EXTRACTOR,
            "PDF original or supported extraction identity differs.")
        return None

    def _snapshot(self, connection, preview_id, actor_user_id, *, now_ms):
        _require(isinstance(connection, ManagedConnection) and connection.in_transaction
            and connection.database_path == self._factory.database_path,
            "PDF source checks require this database's explicit transaction.")
        try:
            valid_id = type(preview_id) is str and str(UUID(preview_id)) == preview_id
        except (ValueError, AttributeError):
            valid_id = False
        _require(valid_id, "Invalid PDF preview identity.")
        continuation_authority = None
        if getattr(self,"_pdf_continuation_source_authority",None) is not None:
            from ..repositories.pdf_visual_continuation import _current_source_authority
            continuation_authority = _current_source_authority(self)
        base = json.loads(WikiPreservationRepository()._source_fence(connection,
            preview_id=preview_id, actor_user_id=actor_user_id, now_ms=now_ms,
            continuation_authority=continuation_authority))
        p = dict(connection.execute("SELECT * FROM publication_preview WHERE id=?", (preview_id,)).fetchone())
        ready = PublicationPreviewRepository().require_for_actor(connection,
            preview_id=preview_id, actor_user_id=actor_user_id).ready_result
        _require(ready is not None, "PDF preview has no validated ready result.")
        parent = self._authority.revalidate_job_authority(connection, p["durable_job_id"])
        PublicationPreviewExtractionHandler.validate_input(parent.input_ref, parent.expected_state)
        expected = parent.expected_state
        _require(set(expected) == {"_authorization_fence", "artifact_id", "preview_id", "media_type",
            "raw_sha256", "raw_size_bytes", "schema_version", "title"},
            "The PDF parent authorization envelope is incomplete.")
        ids = publication_artifact_ids(actor_user_id, parent.operation_id)
        _require(parent.job_type == "publication_preview.extract" and parent.handler_version == "v1"
            and parent.state == "succeeded" and parent.actor_user_id == actor_user_id
            and parent.authorization_part_id == p["captured_part_id"]
            and parent.authorization_scope_epoch == p["captured_scope_epoch"]
            and expected["artifact_id"] == ids.artifact_id == p["artifact_id"]
            and ids.extraction_snapshot_id == p["extraction_snapshot_id"]
            and publication_preview_id(actor_user_id, parent.operation_id) == preview_id
            and parent.input_ref["preview_id"] == expected["preview_id"] == preview_id
            and p["rule_version"] == PUBLICATION_PREVIEW_RULE_VERSION
            and ready["title"] == expected["title"] and ready["operation_id"] == parent.operation_id
            and _json(dict(parent.result)) == _json({"preview_id": preview_id, "schema_version": 1,
                "source_snapshot_sha256": p["source_snapshot_sha256"], "status": "ready"}),
            "PDF parent/result/preview binding differs.")
        source = dict(connection.execute("SELECT * FROM source_artifact WHERE id=?", (p["artifact_id"],)).fetchone())
        extraction = dict(connection.execute("SELECT * FROM extraction_snapshot WHERE id=?",
            (p["extraction_snapshot_id"],)).fetchone())
        _require(source["origin_type"] == "upload" and source["publisher_user_id"] == actor_user_id
            and source["raw_sha256"] == expected["raw_sha256"]
            and type(source["raw_size_bytes"]) is int and 0 < source["raw_size_bytes"] <= self._limits.max_source_bytes
            and source["raw_size_bytes"] == expected["raw_size_bytes"] and source["media_type"] == expected["media_type"]
            and source["media_type"].split(";", 1)[0].lower().strip() in {"application/pdf", "application/octet-stream"}
            and type(source["original_filename"]) is str and PurePath(source["original_filename"]).suffix.lower() == ".pdf"
            and source["metadata_json"] == _json({"schema_version": 1, "ingestion_state": "pending_extraction",
                "raw_sha256": source["raw_sha256"], "raw_size_bytes": source["raw_size_bytes"]}),
            "PDF original or supported extraction identity differs.")
        extension = self._source_extension(connection, source, extraction)
        body = connection.execute("SELECT * FROM extraction_snapshot_body WHERE snapshot_id=?",
            (extraction["id"],)).fetchone()
        _require(body is not None and type(body["content_md"]) is str
            and len(body["content_md"].encode()) == body["content_size_bytes"] == extraction["extracted_size_bytes"]
            and _hash(body["content_md"]) == body["content_sha256"] == extraction["extracted_sha256"]
            and body["created_at"] == extraction["created_at"], "PDF retained extraction body identity differs.")
        _require(ready["size_bytes"] == extraction["extracted_size_bytes"]
            and _json(ready["warnings"]) == extraction["warnings_json"],
            "PDF ready result differs from its retained extraction.")
        blobs = self._retained_blobs(connection, source, extraction, ids)
        fence = {"version": PDF_SOURCE_PREPARATION_VERSION, "base": base, "preview": p,
            "parent": dict(connection.execute("SELECT * FROM durable_job WHERE id=?", (parent.id,)).fetchone()),
            "source": source, "extraction": extraction, "blobs": blobs,
            "extraction_body": {key: body[key] for key in body.keys() if key != "content_md"}}
        if extension is not None:
            fence["source_extension"] = extension
        return _Snapshot(p, source, extraction, _hash(_json(fence)), blobs[1:], extension)

    def _retained_blobs(self, connection, source, extraction, ids):
        """Frozen v1 raw-only policy. Successors supply their own asset validator."""
        blobs = tuple(dict(row) for row in connection.execute(
            """SELECT b.*,bb.content_sha256 AS retained_sha256,bb.created_at AS retained_created_at,
                o.sha256 AS object_sha256,o.size_bytes AS object_size_bytes,o.relative_path,o.verification_status
                FROM artifact_blob b LEFT JOIN artifact_blob_body bb ON bb.artifact_blob_id=b.id
                LEFT JOIN blob_object o ON o.sha256=bb.content_sha256
                WHERE b.artifact_id=? ORDER BY b.ordinal,b.id LIMIT 2""", (source["id"],)))
        _require(len(blobs) == 1,
            "PDF embedded assets require a separate asset-preserving adapter; no silent omission.")
        raw = blobs[0]
        _require(raw["id"] == ids.raw_manifest_id and raw["ordinal"] == 0 and raw["role"] == "raw"
            and raw["body_retention_status"] == "present"
            and raw["content_sha256"] == raw["retained_sha256"] == raw["object_sha256"] == source["raw_sha256"]
            and raw["size_bytes"] == raw["object_size_bytes"] == source["raw_size_bytes"]
            and raw["media_type"] == source["media_type"] and raw["verification_status"] == "verified"
            and raw["relative_path"] == f"{raw['content_sha256'][:2]}/{raw['content_sha256'][2:4]}/{raw['content_sha256']}",
            "PDF retained raw manifest is unavailable or changed.")
        return blobs

    def _read(self, preview_id, actor_user_id, *, now_ms):
        connection = self._factory.open(read_only=True)
        try:
            with transaction(connection, TransactionMode.DEFERRED):
                return self._snapshot(connection, preview_id, actor_user_id, now_ms=now_ms)
        finally:
            connection.close()

    def prepare(self, *, preview_id, actor_user_id, chunk_bytes=8192, model_policy=None,
                work_policy=None, execution_policy=None, now_ms=0, cancel_event=None):
        before = self._read(preview_id, actor_user_id, now_ms=now_ms)
        source = before.source
        leases, group = [], None
        try:
            loaded = self._loader.load(artifact_id=source["id"], expected_sha256=source["raw_sha256"],
                expected_size=source["raw_size_bytes"], expected_media_type=source["media_type"])
            leases.append(loaded.lease)
            _require(loaded.original_filename == source["original_filename"], "PDF source filename changed.")
            original = prepare_pdf_source(before.extraction["id"], loaded.original_filename, loaded.content,
                python_executable=self._python, limits=self._limits, process_policy=self._process_policy,
                cancel_event=cancel_event)
            plan = PdfAuthoringPlan(original, chunk_bytes,
                AuthoringModelPolicy() if model_policy is None else model_policy,
                AuthoringWorkPolicy() if work_policy is None else work_policy,
                PreservationExecutionPolicy() if execution_policy is None else execution_policy)
            after = self._read(preview_id, actor_user_id, now_ms=now_ms)
            _require(before.fence_sha256 == after.fence_sha256, "PDF source changed during preparation.")
            config = _json({"version": PDF_SOURCE_PREPARATION_VERSION, "policy_version": plan.policy_version,
                "prompt_sha256": _hash(PDF_PROMPT), "chunk_bytes": chunk_bytes,
                "model_policy": asdict(plan.model_policy), "work_policy": asdict(plan.work_policy),
                "execution_policy": asdict(plan.execution_policy), "geometry_limits": asdict(self._limits),
                "process_policy": asdict(self._process_policy)})
            group = _Leases(self, leases)
            prepared = PreparedCanonicalPdfSource(preview_id, actor_user_id, before.preview["durable_job_id"],
                source["id"], plan, before.fence_sha256, config, "", group)
            return replace(prepared, seal=_seal(prepared))
        except BaseException:
            if group is not None:
                try:
                    group.close()
                except BaseException:
                    pass  # All releases attempted; preserve the primary failure.
            else:
                for lease in reversed(leases):
                    try:
                        lease.close()
                    except BaseException:
                        pass  # All releases attempted; preserve the primary failure.
            raise

    def revalidate_in_transaction(self, connection, prepared, *, now_ms=0):
        """SQL/CPU only, no transaction ownership or durable claim granted."""
        _require(type(prepared) is PreparedCanonicalPdfSource and prepared._leases.owner is self
            and prepared._leases.token in self._active, "PDF source is foreign or its leases are closed.")
        try:
            valid = type(prepared.seal) is str and hmac.compare_digest(prepared.seal, _seal(prepared))
        except (ValueError, TypeError, AttributeError, UnicodeError, RecursionError):
            valid = False
        _require(valid, "PDF source preparation changed or belongs to another process.")
        current = self._snapshot(connection, prepared.preview_id, prepared.actor_user_id, now_ms=now_ms)
        _require(current.fence_sha256 == prepared.fence_sha256, "PDF source changed before its effect.")
        return prepared

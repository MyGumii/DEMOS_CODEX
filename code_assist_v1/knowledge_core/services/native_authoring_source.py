"""Canonical preparation for the native successor, not a publication endpoint.

Read/authorize -> close SQLite snapshot -> verify retained bytes and reconstruct
the exact extraction/structure/assets -> plan -> reauthorize. A caller must keep
the returned leases open through its transaction and use revalidate_in_transaction
immediately before an effect. No model, enqueue, mutation or raw-text fallback is
performed here. A fresh process reconstructs this preparation; the seal is never
a durable credential or a substitute for a child job's claim fence.
"""
from dataclasses import asdict, dataclass, field, fields, is_dataclass, replace
from hashlib import sha256
import hmac
import json
import secrets
from uuid import UUID, uuid5

from ..db import ManagedConnection, TransactionMode, transaction
from ... import document_pptx_display as pptx_display
from ..native_wiki_authoring import (
    NativeWikiSource, NativeWikiPlan, NativeAssetReference, NativeSlot, MAX_NATIVE_SLOTS,
)
from ..native_wiki_batches import NativeBatchPlan
from ..repositories.native_table_artifacts import (
    NATIVE_EXTRACTOR_VERSION, NATIVE_RENDERER_VERSION, _count_tables,
)
from ..repositories.wiki_preservation import WikiPreservationRepository
from ..wiki_authoring import AuthoringModelPolicy, AuthoringWorkPolicy, _hash, _json
from ..wiki_preservation import PreservationExecutionPolicy
from ..worker import DurableWorkKernel, HandlerRegistry
from .native_publication import (
    NativePublicationPreviewExtractionHandler, NATIVE_PREVIEW_RULE_VERSION,
)
from .native_rich_publication import CanonicalNativePublicationExtractor, native_asset_label
from .publication import (
    CanonicalPublicationSourceLoader, StagedVerifiedBlob,
    publication_artifact_ids, publication_preview_id, _EMBEDDED_ASSET_NAMESPACE,
)


SOURCE_PREPARATION_VERSION = "canonical-native-authoring-source-v1"
DISPLAY_SOURCE_PREPARATION_VERSION = "canonical-native-display-authoring-source-v1"
_KEY = secrets.token_bytes(32)


class NativeSourceConflict(ValueError):
    """Source unavailable, changed or not a native ready upload; no source text."""


class _Leases:
    def __init__(self, owner, leases):
        self.owner, self.leases = owner, tuple(leases)
        self.token = secrets.token_hex(32)
        owner._active.add(self.token)

    def close(self):
        if self.token not in self.owner._active:
            return
        self.owner._active.remove(self.token)
        error = None
        for lease in reversed(self.leases):
            try:
                lease.close()
            except BaseException as exc:
                if error is None:
                    error = exc
        if error is not None:
            raise error


@dataclass(frozen=True, slots=True)
class PreparedNativeAuthoringSource:
    preview_id: str
    actor_user_id: str
    parent_job_id: str
    artifact_id: str
    plan: NativeBatchPlan = field(repr=False)
    fence_sha256: str
    configuration_json: str
    seal: str = field(repr=False)
    _leases: _Leases = field(repr=False, compare=False)
    source_preparation_version: str = SOURCE_PREPARATION_VERSION

    def close(self):
        self._leases.close()

    def __enter__(self):
        if self._leases.token not in self._leases.owner._active:
            raise NativeSourceConflict("Native source preparation has been closed.")
        return self

    def __exit__(self, *_):
        self.close()


@dataclass(frozen=True, slots=True)
class _Snapshot:
    preview: dict
    source: dict
    extraction: dict
    native: dict
    markdown: str = field(repr=False)
    structure_json: str = field(repr=False)
    assets: tuple[dict, ...]
    fence_sha256: str


def _require(condition, message):
    if not condition:
        raise NativeSourceConflict(message)


def _body(row, *, content, digest, size):
    _require(row is not None and type(row[content]) is str,
        "The retained native source body is unavailable.")
    encoded = row[content].encode("utf-8")
    _require(type(row[size]) is int and len(encoded) == row[size]
        and sha256(encoded).hexdigest() == row[digest], "Native body identity differs.")


def _plain(value):
    """Equivalent to asdict for the non-context, source-bounded plan fields."""
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    if type(value) in (tuple, list):
        return [_plain(item) for item in value]
    if type(value) is dict:
        return {key: _plain(item) for key, item in value.items()}
    return value


def _compact_contexts(contexts):
    # Bind the ACTUAL derived caches, not a replacement reconstructed from the
    # advertised plan hash. One tuple per slide prevents asdict from expanding
    # N shared labels N times, including inside the writer revalidation fence.
    _require(type(contexts) is tuple and len(contexts) <= MAX_NATIVE_SLOTS,
        "Native preparation context changed.")
    pool, indices, seen, references = [], [], {}, 0
    for context in contexts:
        _require(type(context) is tuple, "Native preparation context changed.")
        key = id(context)
        if key not in seen:
            references += len(context)
            _require(references <= MAX_NATIVE_SLOTS
                and all(type(slot) is NativeSlot for slot in context),
                "Native preparation context changed or exceeds its bound.")
            seen[key] = len(pool)
            pool.append([asdict(slot) for slot in context])
        indices.append(seen[key])
    return {"unique_contexts": pool, "context_indices": indices}


def _display_plan_record(plan):
    _require(type(plan) is NativeBatchPlan and type(plan.source_plan) is NativeWikiPlan,
        "Native preparation plan changed.")
    p = plan.source_plan
    record = {item.name: _plain(getattr(plan, item.name))
        for item in fields(plan) if item.name != "source_plan"}
    record["source_plan"] = {item.name: (
        _compact_contexts(getattr(p, item.name)) if item.name in ("_heading_contexts", "_display_contexts")
        else _plain(getattr(p, item.name))) for item in fields(p)}
    return record


def _seal(prepared):
    # Full in-memory plan, including derived slots/chunks/reference cache. A
    # convenient plan_sha256 alone would not bind forged Python-derived fields.
    version = prepared.source_preparation_version
    _require(type(version) is str and version in (SOURCE_PREPARATION_VERSION, DISPLAY_SOURCE_PREPARATION_VERSION)
        and (prepared.plan.source_plan.source.display_json is not None)
            == (version == DISPLAY_SOURCE_PREPARATION_VERSION),
        "Native preparation policy changed.")
    private_plan = (_display_plan_record(prepared.plan) if version == DISPLAY_SOURCE_PREPARATION_VERSION
        else asdict(prepared.plan))
    value = {"version": version, "preview_id": prepared.preview_id,
        "actor_user_id": prepared.actor_user_id, "parent_job_id": prepared.parent_job_id,
        "artifact_id": prepared.artifact_id, "fence_sha256": prepared.fence_sha256,
        "configuration_json": prepared.configuration_json,
        "private_plan_sha256": _hash(_json(private_plan)),
        "lease_token": prepared._leases.token}
    return hmac.digest(_KEY, _json(value).encode(), "sha256").hex()


class CanonicalNativeAuthoringSourceService:
    def __init__(self, factory, scope_resolver, stager):
        self._factory, self._stager = factory, stager
        handler = NativePublicationPreviewExtractionHandler(factory, stager,
            scope_resolver=scope_resolver)
        self._authority = DurableWorkKernel(factory,
            HandlerRegistry((handler.registered_handler(),)), scope_resolver=scope_resolver)
        self._loader = CanonicalPublicationSourceLoader(factory, stager)
        self._active = set()

    def _snapshot(self, connection, preview_id, actor_user_id, *, now_ms):
        _require(isinstance(connection, ManagedConnection) and connection.in_transaction
            and connection.database_path == self._factory.database_path,
            "Native source checks require this database's explicit transaction.")
        _require(type(preview_id) is str and str(UUID(preview_id)) == preview_id,
            "Invalid native preview identity.")
        # Existing actor/preview/expiry/extraction-body check, without changing
        # any frozen preservation contract. Complete native relations follow.
        base = json.loads(WikiPreservationRepository()._source_fence(connection,
            preview_id=preview_id, actor_user_id=actor_user_id, now_ms=now_ms))
        p = dict(connection.execute("SELECT * FROM publication_preview WHERE id=?", (preview_id,)).fetchone())
        parent = self._authority.revalidate_job_authority(connection, p["durable_job_id"])
        NativePublicationPreviewExtractionHandler.validate_input(parent.input_ref, parent.expected_state)
        expected = parent.expected_state
        _require(set(expected) == {"_authorization_fence", "artifact_id", "preview_id",
            "media_type", "raw_sha256", "raw_size_bytes", "schema_version", "title", "handler_version"},
            "The native parent authorization envelope is incomplete.")
        ids = publication_artifact_ids(actor_user_id, parent.operation_id)
        _require(parent.job_type == "publication_preview.extract" and parent.handler_version == "v2"
            and parent.state == "succeeded" and parent.actor_user_id == actor_user_id
            and parent.authorization_part_id == p["captured_part_id"]
            and parent.authorization_scope_epoch == p["captured_scope_epoch"]
            and ids.artifact_id == p["artifact_id"] and ids.extraction_snapshot_id == p["extraction_snapshot_id"]
            and publication_preview_id(actor_user_id, parent.operation_id) == preview_id
            and parent.input_ref["preview_id"] == preview_id
            and p["rule_version"] == NATIVE_PREVIEW_RULE_VERSION
            and _json(dict(parent.result)) == _json({"preview_id": preview_id, "schema_version": 2,
                "source_snapshot_sha256": p["source_snapshot_sha256"], "status": "ready"}),
            "Native parent/result/preview binding differs.")
        source = dict(connection.execute("SELECT * FROM source_artifact WHERE id=?", (p["artifact_id"],)).fetchone())
        extraction = dict(connection.execute("SELECT * FROM extraction_snapshot WHERE id=?",
            (p["extraction_snapshot_id"],)).fetchone())
        _require(source["origin_type"] == "upload" and source["publisher_user_id"] == actor_user_id
            and source["raw_sha256"] == expected["raw_sha256"]
            and source["raw_size_bytes"] == expected["raw_size_bytes"]
            and source["media_type"] == expected["media_type"]
            and source["metadata_json"] == _json({"schema_version": 1,
                "ingestion_state": "pending_extraction", "raw_sha256": source["raw_sha256"],
                "raw_size_bytes": source["raw_size_bytes"]})
            and extraction["extractor_version"] == NATIVE_EXTRACTOR_VERSION,
            "Native source/extractor identity differs.")
        native_row = connection.execute("SELECT * FROM extraction_native_table_structure WHERE snapshot_id=?",
            (extraction["id"],)).fetchone()
        _require(native_row is not None, "The native structure descriptor is unavailable.")
        native = dict(native_row)
        body = connection.execute("SELECT * FROM extraction_snapshot_body WHERE snapshot_id=?",
            (extraction["id"],)).fetchone()
        structured = connection.execute("SELECT * FROM extraction_native_table_structure_body WHERE snapshot_id=?",
            (extraction["id"],)).fetchone()
        _body(body, content="content_md", digest="content_sha256", size="content_size_bytes")
        _body(structured, content="structure_json", digest="structure_sha256", size="structure_size_bytes")
        _require(native["artifact_id"] == source["id"] == structured["artifact_id"]
            and native["raw_source_sha256"] == source["raw_sha256"]
            and native["raw_size_bytes"] == source["raw_size_bytes"]
            and native["extracted_markdown_sha256"] == extraction["extracted_sha256"]
            and native["structure_sha256"] == structured["structure_sha256"]
            and native["structure_size_bytes"] == structured["structure_size_bytes"]
            and native["created_at"] == structured["created_at"] == extraction["created_at"]
            and native["renderer_version"] == NATIVE_RENDERER_VERSION,
            "Native structure relations differ.")
        blobs = tuple(dict(row) for row in connection.execute(
            """SELECT b.*,bb.content_sha256 AS retained_sha256,bb.created_at AS retained_created_at,
                o.sha256 AS object_sha256,o.size_bytes AS object_size_bytes,
                o.relative_path,o.verification_status
                FROM artifact_blob b LEFT JOIN artifact_blob_body bb ON bb.artifact_blob_id=b.id
                LEFT JOIN blob_object o ON o.sha256=bb.content_sha256
                WHERE b.artifact_id=? ORDER BY b.ordinal,b.id LIMIT 66""", (source["id"],)))
        _require(1 <= len(blobs) <= 65, "Native asset inventory size differs.")
        for ordinal, blob in enumerate(blobs):
            _require(blob["ordinal"] == ordinal and blob["role"] == ("raw" if ordinal == 0 else "embedded_asset")
                and blob["body_retention_status"] == "present"
                and blob["content_sha256"] == blob["retained_sha256"] == blob["object_sha256"]
                and blob["size_bytes"] == blob["object_size_bytes"]
                and blob["verification_status"] == "verified"
                and blob["relative_path"] == f"{blob['content_sha256'][:2]}/{blob['content_sha256'][2:4]}/{blob['content_sha256']}",
                "Native retained asset inventory is incomplete or changed.")
            if ordinal:
                manifest_id = str(uuid5(_EMBEDDED_ASSET_NAMESPACE,
                    f"{source['id']}:{ordinal}:{blob['content_sha256']}:{blob['logical_name']}:{blob['media_type']}"))
                _require(blob["id"] == manifest_id
                    and blob["created_at"] == blob["retained_created_at"] == extraction["created_at"],
                    "Native asset ordinal/creation binding differs.")
        raw = blobs[0]
        _require(raw["id"] == ids.raw_manifest_id
            and raw["content_sha256"] == source["raw_sha256"]
            and raw["size_bytes"] == source["raw_size_bytes"]
            and raw["media_type"] == source["media_type"], "Native raw manifest differs.")
        fence = {"version": SOURCE_PREPARATION_VERSION, "base": base, "preview": p,
            "parent": dict(connection.execute("SELECT * FROM durable_job WHERE id=?", (parent.id,)).fetchone()),
            "source": source, "extraction": extraction, "native": native, "blobs": blobs,
            "extraction_body": {key: body[key] for key in body.keys() if key != "content_md"},
            "native_body": {key: structured[key] for key in structured.keys() if key != "structure_json"}}
        return _Snapshot(p, source, extraction, native, body["content_md"], structured["structure_json"],
            blobs[1:], _hash(_json(fence)))

    def _read(self, preview_id, actor_user_id, *, now_ms):
        connection = self._factory.open(read_only=True)
        try:
            with transaction(connection, TransactionMode.DEFERRED):
                return self._snapshot(connection, preview_id, actor_user_id, now_ms=now_ms)
        finally:
            connection.close()

    def _verify_asset(self, row, content, leases):
        observation, retained = self._stager.verify_existing(expected_sha256=row["content_sha256"],
            expected_size=row["size_bytes"], media_type=row["media_type"],
            normalized_content_sha256=None, normalizer_version=None)
        lease, blob = getattr(observation, "lease", None), getattr(observation, "blob", None)
        if callable(getattr(lease, "close", None)):
            leases.append(lease)
        _require(type(retained) is bytes and retained == content and type(blob) is StagedVerifiedBlob
            and blob.sha256 == row["content_sha256"] and type(blob.size_bytes) is int
            and blob.size_bytes == row["size_bytes"]
            and blob.media_type == row["media_type"] and blob.relative_path == row["relative_path"]
            and type(blob.created_at_ms) is int and blob.created_at_ms == 0
            and type(blob.verified_at_ms) is int and blob.verified_at_ms == 0
            and callable(getattr(lease, "close", None)), "Native asset bytes/verifier evidence differ.")

    def prepare(self, *, preview_id, actor_user_id, chunk_bytes=8192, context_bytes=8192,
            model_policy=None, work_policy=None, execution_policy=None, now_ms=0,
            source_preparation_version=SOURCE_PREPARATION_VERSION):
        _require(type(source_preparation_version) is str and source_preparation_version in
            (SOURCE_PREPARATION_VERSION, DISPLAY_SOURCE_PREPARATION_VERSION),
            "Unknown native source preparation policy.")
        before = self._read(preview_id, actor_user_id, now_ms=now_ms)
        source, descriptor = before.source, before.native
        leases = []
        lease_group = None
        try:
            loaded = self._loader.load(artifact_id=source["id"], expected_sha256=source["raw_sha256"],
                expected_size=source["raw_size_bytes"], expected_media_type=source["media_type"])
            leases.append(loaded.lease)
            _require(loaded.original_filename == source["original_filename"], "Native source filename changed.")
            # This is source reconstruction, not parsing arbitrary extraction
            # Markdown or asking another LLM to verify/repair a draft.
            native = CanonicalNativePublicationExtractor().extract_native_source(loaded.content,
                media_type=loaded.media_type, original_filename=loaded.original_filename)
            outcome, document = native.extraction, native.document
            _require(document.canonical_json() == before.structure_json
                and document.version == descriptor["structure_version"]
                and document.format == descriptor["source_format"]
                and document.coverage == descriptor["coverage"]
                and _count_tables(document) == descriptor["table_count"]
                and outcome.markdown == before.markdown
                and outcome.extractor_name == before.extraction["extractor_name"]
                and outcome.extractor_version == before.extraction["extractor_version"]
                and _json(list(outcome.warnings)) == before.extraction["warnings_json"]
                and type(native.asset_references) is tuple
                and len(native.asset_references) == len(outcome.assets) == len(before.assets),
                "The retained extraction/structure differs from the exact original.")
            for ordinal, (asset, row, reference) in enumerate(zip(outcome.assets,
                    before.assets, native.asset_references, strict=True), 1):
                _require(asset.logical_name == row["logical_name"] and asset.media_type == row["media_type"]
                    and len(asset.content) == row["size_bytes"]
                    and sha256(asset.content).hexdigest() == row["content_sha256"],
                    "The retained asset differs from the exact original.")
                _require(type(reference) is NativeAssetReference
                    and reference.sha256 == row["content_sha256"]
                    and reference.label == native_asset_label(document.format, ordinal),
                    "Native asset display references differ from the retained source.")
                self._verify_asset(row, asset.content, leases)
            display_json = None
            if source_preparation_version == DISPLAY_SOURCE_PREPARATION_VERSION:
                _require(document.format == "pptx", "Native display preparation requires PPTX.")
                # No caller-supplied companion/formatting is trusted. Reconstruct
                # from the same authorized, leased original and compare the full
                # native descriptor already verified against retained extraction.
                actual_document, companion = pptx_display.extract_pptx_display_source(
                    loaded.original_filename, loaded.content)
                _require(actual_document.canonical_json() == before.structure_json,
                    "The display source differs from the exact retained original.")
                display_json = companion.canonical_json()
            plan = NativeBatchPlan(NativeWikiPlan(NativeWikiSource(before.extraction["id"],
                before.extraction["extracted_sha256"], before.structure_json,
                descriptor["structure_sha256"], native.asset_references, display_json),
                chunk_bytes=chunk_bytes, context_bytes=context_bytes,
                model_policy=AuthoringModelPolicy() if model_policy is None else model_policy,
                work_policy=AuthoringWorkPolicy() if work_policy is None else work_policy,
                execution_policy=PreservationExecutionPolicy() if execution_policy is None else execution_policy))
            after = self._read(preview_id, actor_user_id, now_ms=now_ms)
            _require(before.fence_sha256 == after.fence_sha256, "Native source changed during preparation.")
            config = _json({"version": source_preparation_version, "chunk_bytes": chunk_bytes,
                "context_bytes": context_bytes, "model_policy": asdict(plan.source_plan.model_policy),
                "work_policy": asdict(plan.source_plan.work_policy),
                "execution_policy": asdict(plan.source_plan.execution_policy)})
            lease_group = _Leases(self, leases)
            prepared = PreparedNativeAuthoringSource(preview_id, actor_user_id,
                before.preview["durable_job_id"], source["id"], plan, before.fence_sha256,
                config, "", lease_group, source_preparation_version)
            return replace(prepared, seal=_seal(prepared))
        except BaseException:
            if lease_group is not None:
                lease_group.close()
            else:
                error = None
                for lease in reversed(leases):
                    try:
                        lease.close()
                    except BaseException as exc:
                        error = error or exc
                # Preserve the source/preparation failure; every lease was tried.
            raise

    def revalidate_in_transaction(self, connection, prepared, *, now_ms=0):
        """No I/O/model/parser or transaction ownership; no durable claim granted."""
        _require(type(prepared) is PreparedNativeAuthoringSource
            and prepared._leases.owner is self and prepared._leases.token in self._active,
            "Native preparation is foreign or its leases are closed.")
        try:
            valid = type(prepared.seal) is str and hmac.compare_digest(prepared.seal, _seal(prepared))
        except (ValueError, TypeError, AttributeError, UnicodeError, RecursionError):
            valid = False
        _require(valid, "Native preparation changed or belongs to another process.")
        current = self._snapshot(connection, prepared.preview_id, prepared.actor_user_id, now_ms=now_ms)
        _require(current.fence_sha256 == prepared.fence_sha256, "Native source changed before its effect.")
        return prepared


__all__ = ["CanonicalNativeAuthoringSourceService", "PreparedNativeAuthoringSource",
    "NativeSourceConflict", "SOURCE_PREPARATION_VERSION", "DISPLAY_SOURCE_PREPARATION_VERSION"]

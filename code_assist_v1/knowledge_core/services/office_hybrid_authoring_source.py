"""Restore an exact v3 Office preview as native and page-visual Wiki input.

This is an opt-in preparation boundary, not a publication authority or route.
The original CAS lease stays open through a caller's effect transaction. Every
preparation and revalidation checks the v3 request, retained native extraction,
complete page archive, current user epoch, and export authorization.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from hashlib import sha256
import hmac
import json
import secrets

from ..db import ManagedConnection, TransactionMode, transaction
from ..migrations.office_hybrid_preview_catalog import OFFICE_HYBRID_PREVIEW_MIGRATION_CATALOG
from ..migrations.v055_office_hybrid_preview import RULE
from ..native_wiki_authoring import NativeWikiPlan, NativeWikiSource
from ..native_wiki_batches import NativeBatchPlan
from ..office_hybrid_visual_authoring import OfficeHybridVisualPlan
from ..repositories.artifacts import StagedVerifiedBlob
from ..repositories.native_table_artifacts import NATIVE_EXTRACTOR_VERSION, NATIVE_RENDERER_VERSION
from ..repositories.office_hybrid_visual_archive import OfficeHybridVisualArchiveRepository
from ..repositories.wiki_preservation import WikiPreservationRepository
from ..wiki_authoring import _hash, _json
from .native_authoring_source import _Leases
from .native_rich_publication import CanonicalNativePublicationExtractor
from .office_hybrid_preview import OfficeHybridUploadPolicy
from .publication import CanonicalPublicationSourceLoader


_KEY = secrets.token_bytes(32)


class OfficeHybridAuthoringSourceConflict(ValueError):
    """The retained ready v3 source or its export right is unavailable."""


def _require(ok, message="Office hybrid authoring source changed or is unavailable."):
    if not ok:
        raise OfficeHybridAuthoringSourceConflict(message)


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


@dataclass(frozen=True, slots=True)
class PreparedOfficeHybridAuthoringSource:
    preview_id: str
    actor_user_id: str
    parent_job_id: str
    artifact_id: str
    original_filename: str
    original: bytes = field(repr=False)
    native: NativeBatchPlan = field(repr=False)
    visual: OfficeHybridVisualPlan = field(repr=False)
    fence_sha256: str
    seal: str = field(repr=False)
    _leases: _Leases = field(repr=False, compare=False)

    def close(self):
        self._leases.close()

    def __enter__(self):
        _require(self._leases.token in self._leases.owner._active,
            "Office hybrid source preparation is closed.")
        return self

    def __exit__(self, *_):
        self.close()


def _seal(prepared):
    prepared.visual.source.check_seal()
    record = {"preview_id": prepared.preview_id,
        "actor_user_id": prepared.actor_user_id,
        "parent_job_id": prepared.parent_job_id,
        "artifact_id": prepared.artifact_id,
        "original_filename": prepared.original_filename,
        "original_sha256": sha256(prepared.original).hexdigest(),
        "native": asdict(prepared.native),
        "visual": {"plan_sha256": prepared.visual.plan_sha256,
            "source": prepared.visual.source.identity(),
            "model_policy": asdict(prepared.visual.model_policy),
            "execution_policy": asdict(prepared.visual.execution_policy)},
        "fence_sha256": prepared.fence_sha256,
        "lease_token": prepared._leases.token}
    return hmac.digest(_KEY, _json(record).encode(), "sha256").hex()


class CanonicalOfficeHybridAuthoringSourceService:
    def __init__(self, factory, stager, *, archive: OfficeHybridVisualArchiveRepository,
            policy: OfficeHybridUploadPolicy):
        expected = OFFICE_HYBRID_PREVIEW_MIGRATION_CATALOG.migrations
        if factory.migration_catalog.migrations[:len(expected)] != expected:
            raise ValueError("Office hybrid source requires its verified v55 catalog.")
        if (type(archive) is not OfficeHybridVisualArchiveRepository
                or archive.factory.database_path != factory.database_path
                or type(policy) is not OfficeHybridUploadPolicy):
            raise TypeError("Office hybrid source requires same-database archive and policy.")
        self.factory, self.archive, self.policy = factory, archive, policy
        self.stager = stager
        self.loader = CanonicalPublicationSourceLoader(factory, stager)
        self._active = set()

    def _snapshot(self, connection: ManagedConnection, preview_id, actor_user_id, *, now_ms):
        _require(connection.in_transaction and connection.database_path == self.factory.database_path)
        base = json.loads(WikiPreservationRepository()._source_fence(connection,
            preview_id=preview_id, actor_user_id=actor_user_id, now_ms=now_ms))
        preview = dict(connection.execute("SELECT * FROM publication_preview WHERE id=?",
            (preview_id,)).fetchone())
        source = dict(connection.execute("SELECT * FROM source_artifact WHERE id=?",
            (preview["artifact_id"],)).fetchone())
        extraction = dict(connection.execute("SELECT * FROM extraction_snapshot WHERE id=?",
            (preview["extraction_snapshot_id"],)).fetchone())
        parent = dict(connection.execute("SELECT * FROM durable_job WHERE id=?",
            (preview["durable_job_id"],)).fetchone())
        request_row = connection.execute("SELECT * FROM office_hybrid_upload_request WHERE preview_id=?",
            (preview_id,)).fetchone()
        _require(request_row is not None)
        request = dict(request_row)
        native_row = connection.execute("""SELECT * FROM extraction_native_table_structure
            WHERE snapshot_id=? AND artifact_id=?""",
            (extraction["id"], source["id"])).fetchone()
        body_row = connection.execute("""SELECT * FROM extraction_snapshot_body
            WHERE snapshot_id=? AND artifact_id=?""",
            (extraction["id"], source["id"])).fetchone()
        structure_row = connection.execute("""SELECT * FROM extraction_native_table_structure_body
            WHERE snapshot_id=? AND artifact_id=?""",
            (extraction["id"], source["id"])).fetchone()
        _require(native_row is not None and body_row is not None and structure_row is not None)
        native, body, structure = dict(native_row), dict(body_row), dict(structure_row)
        archive = self.archive._load(connection, preview_id=preview_id,
            actor_user_id=actor_user_id)
        assets = tuple(dict(row) for row in connection.execute("""SELECT b.ordinal,b.logical_name,
            b.media_type,b.content_sha256,b.size_bytes,bb.content_sha256 AS retained_sha256,
            o.sha256 AS object_sha256,o.size_bytes AS object_size_bytes,
            o.verification_status
            FROM artifact_blob b
            LEFT JOIN artifact_blob_body bb ON bb.artifact_blob_id=b.id
            LEFT JOIN blob_object o ON o.sha256=bb.content_sha256
            WHERE b.artifact_id=? AND b.role='embedded_asset' ORDER BY b.ordinal""",
            (source["id"],)))
        expected = json.loads(parent["expected_state_json"])
        input_ref = json.loads(parent["input_ref_json"])
        result = json.loads(parent["result_json"])
        expected_policy = self.policy.encoded()
        _require(preview["status"] == "ready" and preview["rule_version"] == RULE
            and parent["state"] == "succeeded" and parent["job_type"] == "publication_preview.extract"
            and parent["handler_version"] == "v3" and parent["actor_user_id"] == actor_user_id
            and parent["authorization_part_id"] == preview["captured_part_id"]
            and parent["authorization_scope_epoch"] == preview["captured_scope_epoch"]
            and input_ref["preview_id"] == preview_id
            and input_ref["artifact_id"] == source["id"]
            and expected["schema_version"] == 3
            and expected["handler_version"] == "v3"
            and expected["raw_sha256"] == source["raw_sha256"]
            and expected["raw_size_bytes"] == source["raw_size_bytes"]
            and expected["media_type"] == source["media_type"]
            and result == {"preview_id": preview_id, "schema_version": 3,
                "source_snapshot_sha256": preview["source_snapshot_sha256"],
                "status": "ready"}
            and request["parent_job_id"] == parent["id"]
            and request["actor_user_id"] == actor_user_id
            and request["artifact_id"] == source["id"]
            and request["created_at"] == parent["created_at"]
            and request["policy_json"] == expected_policy
            and request["policy_sha256"] == _hash(expected_policy)
            and source["publisher_user_id"] == actor_user_id
            and source["raw_sha256"] == archive[0][4]
            and archive[0][2] == source["id"]
            and archive[0][3] == extraction["id"]
            and extraction["id"] == preview["extraction_snapshot_id"]
            and extraction["artifact_id"] == source["id"]
            and extraction["status"] == "ready"
            and extraction["extractor_version"] == NATIVE_EXTRACTOR_VERSION
            and native["renderer_version"] == NATIVE_RENDERER_VERSION
            and native["structure_sha256"] == archive[0][6]
            and native["extracted_markdown_sha256"] == extraction["extracted_sha256"]
            and native["structure_sha256"] == structure["structure_sha256"]
            and native["artifact_id"] == source["id"]
            and type(body["content_md"]) is str
            and sha256(body["content_md"].encode()).hexdigest() == extraction["extracted_sha256"]
            and type(structure["structure_json"]) is str
            and len(structure["structure_json"].encode()) == structure["structure_size_bytes"]
            and sha256(structure["structure_json"].encode()).hexdigest() == native["structure_sha256"]
            and all(row["ordinal"] == index
                and row["content_sha256"] == row["retained_sha256"] == row["object_sha256"]
                and row["size_bytes"] == row["object_size_bytes"]
                and row["verification_status"] == "verified"
                for index, row in enumerate(assets, 1)))
        fence = _hash(_json({"base": base, "preview": preview, "source": source,
            "extraction": extraction, "parent": parent, "request": request,
            "native": native,
            "extraction_body": {key: body[key] for key in body if key != "content_md"},
            "native_body": {key: structure[key] for key in structure if key != "structure_json"},
            "assets": assets, "archive": archive}))
        return _Snapshot(preview, source, extraction, native, body["content_md"],
            structure["structure_json"], assets, fence)

    def _read(self, preview_id, actor_user_id, *, now_ms):
        connection = self.factory.open(read_only=True)
        try:
            with transaction(connection, TransactionMode.DEFERRED):
                return self._snapshot(connection, preview_id, actor_user_id, now_ms=now_ms)
        finally:
            connection.close()

    def prepare(self, *, preview_id, actor_user_id, now_ms=0):
        before = self._read(preview_id, actor_user_id, now_ms=now_ms)
        loaded, leases = None, None
        asset_leases = []
        try:
            loaded = self.loader.load(artifact_id=before.source["id"],
                expected_sha256=before.source["raw_sha256"],
                expected_size=before.source["raw_size_bytes"],
                expected_media_type=before.source["media_type"])
            _require(loaded.original_filename == before.source["original_filename"])
            visual_source = self.archive.restore_source(preview_id=preview_id,
                actor_user_id=actor_user_id, original_filename=loaded.original_filename,
                original=loaded.content)
            native = CanonicalNativePublicationExtractor().extract_native_source(
                loaded.content, media_type=loaded.media_type,
                original_filename=loaded.original_filename,
                verified_visual_source=visual_source, actor_user_id=actor_user_id)
            _require(native.document.canonical_json() == before.structure_json
                and native.extraction.markdown == before.markdown
                and native.extraction.extractor_name == before.extraction["extractor_name"]
                and native.extraction.extractor_version == before.extraction["extractor_version"]
                and len(native.extraction.assets) == len(before.assets))
            for index, (asset, row) in enumerate(zip(native.extraction.assets,
                    before.assets, strict=True), 1):
                _require(row["ordinal"] == index and asset.logical_name == row["logical_name"]
                    and asset.media_type == row["media_type"]
                    and len(asset.content) == row["size_bytes"]
                    and sha256(asset.content).hexdigest() == row["content_sha256"])
                observation, retained = self.stager.verify_existing(
                    expected_sha256=row["content_sha256"],
                    expected_size=row["size_bytes"], media_type=row["media_type"],
                    normalized_content_sha256=None, normalizer_version=None)
                lease, blob = getattr(observation, "lease", None), getattr(observation, "blob", None)
                if callable(getattr(lease, "close", None)):
                    asset_leases.append(lease)
                _require(retained == asset.content and type(blob) is StagedVerifiedBlob
                    and blob.sha256 == row["content_sha256"]
                    and blob.size_bytes == row["size_bytes"]
                    and blob.media_type == row["media_type"]
                    and callable(getattr(lease, "close", None)),
                    "Retained Office embedded asset differs from its native source.")
            native_plan = NativeBatchPlan(NativeWikiPlan(NativeWikiSource(
                before.extraction["id"], before.extraction["extracted_sha256"],
                before.structure_json, before.native["structure_sha256"],
                native.asset_references),
                chunk_bytes=self.policy.native.chunk_bytes,
                context_bytes=self.policy.native.context_bytes,
                model_policy=self.policy.native.model,
                work_policy=self.policy.native.work,
                execution_policy=self.policy.native.execution))
            visual_plan = OfficeHybridVisualPlan(visual_source,
                model_policy=self.policy.visual_model,
                execution_policy=self.policy.native.execution)
            after = self._read(preview_id, actor_user_id, now_ms=now_ms)
            _require(before.fence_sha256 == after.fence_sha256)
            leases = _Leases(self, (loaded.lease, *asset_leases))
            prepared = PreparedOfficeHybridAuthoringSource(preview_id, actor_user_id,
                before.preview["durable_job_id"], before.source["id"],
                loaded.original_filename, loaded.content, native_plan, visual_plan,
                before.fence_sha256, "", leases)
            return replace(prepared, seal=_seal(prepared))
        except BaseException:
            if leases is not None:
                leases.close()
            else:
                for lease in reversed(asset_leases):
                    lease.close()
                if loaded is not None:
                    loaded.lease.close()
            raise

    def revalidate_in_transaction(self, connection, prepared, *, now_ms=0):
        _require(type(prepared) is PreparedOfficeHybridAuthoringSource
            and prepared._leases.owner is self
            and prepared._leases.token in self._active,
            "Office hybrid source lease is closed or foreign.")
        try:
            valid = hmac.compare_digest(prepared.seal, _seal(prepared))
        except (ValueError, TypeError, AttributeError, UnicodeError, RecursionError):
            valid = False
        _require(valid, "Office hybrid source preparation changed.")
        current = self._snapshot(connection, prepared.preview_id,
            prepared.actor_user_id, now_ms=now_ms)
        _require(current.fence_sha256 == prepared.fence_sha256,
            "Office hybrid source changed before its effect.")
        self.archive._authorize(actor_user_id=prepared.actor_user_id,
            filename=prepared.original_filename, original=prepared.original)
        return prepared


__all__ = ["CanonicalOfficeHybridAuthoringSourceService",
    "PreparedOfficeHybridAuthoringSource", "OfficeHybridAuthoringSourceConflict"]

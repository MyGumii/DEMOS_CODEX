"""Authorized retained-PPTX preparation for role writing, opt-in only.

This capability is deliberately not PreparedNativeAuthoringSource/NativeBatchPlan.
It performs no mutation, model call, enqueue or publication. Original bytes and
assets are reconstructed outside SQLite, then current authority is rechecked.
The process-local seal includes actual derived plan caches, not just its ID.
"""
from dataclasses import asdict, dataclass, field, fields, is_dataclass, replace
from hashlib import sha256
import hmac
import secrets

from ..native_wiki_authoring import NativeWikiSource, NativeAssetReference
from ..pptx_role_authoring import (
    PptxRoleSource, PptxRoleAuthoringPlan, ROLE_AUTHORING_V2_VERSION, MAX_RAW_BYTES,
)
from ..wiki_authoring import AuthoringModelPolicy, AuthoringWorkPolicy, _hash, _json
from ..wiki_preservation import PreservationExecutionPolicy
from .native_authoring_source import (
    CanonicalNativeAuthoringSourceService, NativeSourceConflict, _Leases, _require,
)
from .native_rich_publication import CanonicalNativePublicationExtractor, native_asset_label
from ..repositories.native_table_artifacts import _count_tables
from ...document_pptx_role_context import extract_pptx_role_context


ROLE_SOURCE_PREPARATION_VERSION = "canonical-pptx-role-authoring-source-v1"
MAX_SEAL_NODES = 1_000_000
MAX_SEAL_TEXT_BYTES = 64 * 1024 * 1024
_KEY = secrets.token_bytes(32)


@dataclass(frozen=True, slots=True)
class PreparedPptxRoleSource:
    preview_id: str
    actor_user_id: str
    parent_job_id: str
    artifact_id: str
    plan: PptxRoleAuthoringPlan = field(repr=False)
    fence_sha256: str
    configuration_json: str
    seal: str = field(repr=False)
    _leases: _Leases = field(repr=False, compare=False)
    source_preparation_version: str = ROLE_SOURCE_PREPARATION_VERSION

    def close(self):
        self._leases.close()

    def __enter__(self):
        _require(self._leases.token in self._leases.owner._active,
            "PPTX role source preparation has been closed.")
        return self

    def __exit__(self, *_):
        self.close()


def _private_plan_digest(plan):
    """Bounded, typed encoding of actual fields, including compare=False caches.

    Raw bytes are hashed instead of copied into JSON. Dict keys and container
    types are explicit so bool/int and tuple/list aliases cannot share a seal.
    This is CPU-only; no reconstruction, parser, filesystem or model is invoked
    in a caller's writer transaction.
    """
    _require(type(plan) is PptxRoleAuthoringPlan, "PPTX role preparation plan changed.")
    nodes, text_bytes = 0, 0

    def walk(value, depth=0):
        nonlocal nodes, text_bytes
        nodes += 1
        _require(nodes <= MAX_SEAL_NODES and depth <= 64,
            "PPTX role preparation seal exceeds its work bound.")
        if value is None:
            return ["none"]
        if type(value) is bool:
            return ["bool", value]
        if type(value) is int:
            return ["int", value]
        if type(value) is str:
            text_bytes += len(value.encode("utf-8"))
            _require(text_bytes <= MAX_SEAL_TEXT_BYTES,
                "PPTX role preparation text exceeds its seal bound.")
            return ["str", value]
        if type(value) is bytes:
            _require(len(value) <= MAX_RAW_BYTES, "PPTX role preparation raw bytes changed.")
            return ["bytes", len(value), sha256(value).hexdigest()]
        if type(value) in (tuple, list):
            _require(len(value) <= MAX_SEAL_NODES - nodes,
                "PPTX role preparation container exceeds its seal bound.")
            return [type(value).__name__, [walk(v, depth + 1) for v in value]]
        if type(value) is dict:
            _require(len(value) * 2 <= MAX_SEAL_NODES - nodes,
                "PPTX role preparation map exceeds its seal bound.")
            entries = [(walk(k, depth + 1), walk(v, depth + 1)) for k, v in value.items()]
            return ["dict", sorted(entries, key=lambda pair: _json(pair[0]))]
        if is_dataclass(value) and not isinstance(value, type):
            return [type(value).__module__ + "." + type(value).__qualname__,
                [[f.name, walk(getattr(value, f.name), depth + 1)] for f in fields(value)]]
        raise NativeSourceConflict("PPTX role preparation contains an unsupported value.")

    return _hash(_json({"plan": walk(plan), "prompt": walk(plan.prompt)}))


def _seal(prepared):
    _require(type(prepared) is PreparedPptxRoleSource
        and prepared.source_preparation_version == ROLE_SOURCE_PREPARATION_VERSION,
        "PPTX role source preparation policy changed.")
    record = {"version": prepared.source_preparation_version,
        "preview_id": prepared.preview_id, "actor_user_id": prepared.actor_user_id,
        "parent_job_id": prepared.parent_job_id, "artifact_id": prepared.artifact_id,
        "fence_sha256": prepared.fence_sha256, "configuration_json": prepared.configuration_json,
        "private_plan_sha256": _private_plan_digest(prepared.plan),
        "lease_token": prepared._leases.token}
    return hmac.digest(_KEY, _json(record).encode(), "sha256").hex()


class CanonicalPptxRoleSourceService:
    def __init__(self, factory, scope_resolver, stager):
        # Reuse the frozen parent/source/asset authorization checks, not its
        # old writer planning. A valid role plan need not fit legacy slot rules.
        self._base = CanonicalNativeAuthoringSourceService(factory, scope_resolver, stager)
        self._active = set()

    def prepare(self, *, preview_id, actor_user_id, chunk_bytes=8192, context_bytes=8192,
            model_policy=None, work_policy=None, execution_policy=None, now_ms=0,
            policy_version=ROLE_AUTHORING_V2_VERSION):
        before = self._base._read(preview_id, actor_user_id, now_ms=now_ms)
        source, descriptor = before.source, before.native
        _require(descriptor["source_format"] == "pptx"
            and type(source["raw_size_bytes"]) is int and source["raw_size_bytes"] <= MAX_RAW_BYTES,
            "PPTX role preparation requires a bounded retained PPTX.")
        leases, group = [], None
        try:
            loaded = self._base._loader.load(artifact_id=source["id"], expected_sha256=source["raw_sha256"],
                expected_size=source["raw_size_bytes"], expected_media_type=source["media_type"])
            leases.append(loaded.lease)
            _require(loaded.original_filename == source["original_filename"], "PPTX role source filename changed.")
            native = CanonicalNativePublicationExtractor().extract_native_source(loaded.content,
                media_type=loaded.media_type, original_filename=loaded.original_filename)
            outcome, document = native.extraction, native.document
            _require(document.canonical_json() == before.structure_json
                and document.version == descriptor["structure_version"] and document.format == "pptx"
                and document.coverage == descriptor["coverage"]
                and _count_tables(document) == descriptor["table_count"]
                and outcome.markdown == before.markdown
                and outcome.extractor_name == before.extraction["extractor_name"]
                and outcome.extractor_version == before.extraction["extractor_version"]
                and _json(list(outcome.warnings)) == before.extraction["warnings_json"]
                and type(native.asset_references) is tuple
                and len(native.asset_references) == len(outcome.assets) == len(before.assets),
                "The retained PPTX extraction differs from the exact original.")
            for ordinal, (asset, row, reference) in enumerate(zip(outcome.assets,
                    before.assets, native.asset_references, strict=True), 1):
                _require(asset.logical_name == row["logical_name"] and asset.media_type == row["media_type"]
                    and len(asset.content) == row["size_bytes"] and sha256(asset.content).hexdigest() == row["content_sha256"]
                    and type(reference) is NativeAssetReference and reference.sha256 == row["content_sha256"]
                    and reference.label == native_asset_label("pptx", ordinal),
                    "PPTX role source assets differ from the exact original.")
                self._base._verify_asset(row, asset.content, leases)
            reconstructed, display, _ = extract_pptx_role_context(loaded.original_filename, loaded.content)
            _require(reconstructed.canonical_json() == before.structure_json,
                "PPTX role context differs from the retained original.")
            role_source = PptxRoleSource(NativeWikiSource(before.extraction["id"],
                before.extraction["extracted_sha256"], before.structure_json, descriptor["structure_sha256"],
                native.asset_references, display.canonical_json()), loaded.original_filename, loaded.content)
            plan = PptxRoleAuthoringPlan(role_source, chunk_bytes, context_bytes,
                AuthoringModelPolicy() if model_policy is None else model_policy,
                AuthoringWorkPolicy() if work_policy is None else work_policy,
                PreservationExecutionPolicy() if execution_policy is None else execution_policy, policy_version)
            after = self._base._read(preview_id, actor_user_id, now_ms=now_ms)
            _require(before.fence_sha256 == after.fence_sha256, "PPTX role source changed during preparation.")
            configuration = _json({"version": ROLE_SOURCE_PREPARATION_VERSION,
                "policy_version": plan.policy_version, "prompt_sha256": _hash(plan.prompt),
                "chunk_bytes": chunk_bytes, "context_bytes": context_bytes,
                "model_policy": asdict(plan.model_policy), "work_policy": asdict(plan.work_policy),
                "execution_policy": asdict(plan.execution_policy)})
            group = _Leases(self, leases)
            prepared = PreparedPptxRoleSource(preview_id, actor_user_id, before.preview["durable_job_id"],
                source["id"], plan, before.fence_sha256, configuration, "", group)
            return replace(prepared, seal=_seal(prepared))
        except BaseException:
            if group is not None:
                group.close()
            else:
                for lease in reversed(leases):
                    try:
                        lease.close()
                    except BaseException:
                        pass  # Try all releases while retaining the primary failure.
            raise

    def revalidate_in_transaction(self, connection, prepared, *, now_ms=0):
        _require(type(prepared) is PreparedPptxRoleSource and prepared._leases.owner is self
            and prepared._leases.token in self._active,
            "PPTX role source is foreign or its leases are closed.")
        try:
            valid = type(prepared.seal) is str and hmac.compare_digest(prepared.seal, _seal(prepared))
        except (ValueError, TypeError, AttributeError, UnicodeError, RecursionError):
            valid = False
        _require(valid, "PPTX role source preparation changed or belongs to another process.")
        current = self._base._snapshot(connection, prepared.preview_id, prepared.actor_user_id, now_ms=now_ms)
        _require(current.fence_sha256 == prepared.fence_sha256, "PPTX role source changed before its effect.")
        return prepared

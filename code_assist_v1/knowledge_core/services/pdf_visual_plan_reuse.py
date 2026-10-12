"""One service-local, bounded ordinary plan; never an archive or source lease.

Only a fully restored ordinary PDF may enter. A lookup still follows fresh
authorization, CAS/raw/asset and configuration checks in the restore service.
Its public descriptor is a lookup key, NOT evidence of admission: each hit
checks the previously issued private whole-plan seal against current nested
fields and pixels. No worker, model, durable state, or semantic policy lives
here. Historical and protected preparations never enter this cache.
"""
from dataclasses import dataclass, fields, is_dataclass
from hashlib import sha256
import hmac
import secrets
import sys
from threading import Lock

from ..wiki_authoring import _json


MAXIMUM_OWNED_BYTES = 134_217_728


class PdfVisualPlanReuseConflict(ValueError):
    pass


def _owned_bytes(value, *, maximum=MAXIMUM_OWNED_BYTES):
    """Conservative unique Python object storage, including declared caches.

    No persistent size/hash memo is trusted. Over-budget/unknown/cyclic trees
    are not cached; that does not reject otherwise admitted cold preparation.
    """
    seen, active = set(), set()
    total, nodes = 1024, 0  # Entry/lock/key reference overhead, conservatively.

    def visit(item, depth):
        nonlocal total, nodes
        if depth > 64 or id(item) in active:
            raise ValueError("Uncacheable plan storage.")
        if id(item) in seen:
            return
        nodes += 1
        if nodes > 200_000:
            raise ValueError("Uncacheable plan storage.")
        kind = type(item)
        if item is None or kind in (bool, int, float, str, bytes):
            children = ()
        elif kind in (tuple, list):
            children = item
        elif kind is dict:
            children = (part for pair in item.items() for part in pair)
        elif is_dataclass(item) and not isinstance(item, type):
            children = (getattr(item, field.name) for field in fields(item))
            # Non-slotted dataclasses own their attribute dictionary too.
            owned_dict = getattr(item, "__dict__", None)
            if owned_dict is not None:
                total += sys.getsizeof(owned_dict)
        else:
            raise ValueError("Uncacheable plan storage.")
        total += sys.getsizeof(item)
        if total > maximum:
            raise ValueError("Plan storage exceeds reuse bound.")
        seen.add(id(item))
        active.add(id(item))
        for child in children:
            visit(child, depth + 1)
        active.remove(id(item))

    visit(value, 0)
    return total


@dataclass(frozen=True, slots=True)
class _Entry:
    key: tuple
    plan: object
    owned_bytes: int
    seal: str
    inventory: tuple | None = None


def _entry_seal(entry, key):
    # Compare the original entry attestation, never issue a new one on a hit.
    # The key itself contains no raw PDF or live archive/lease object.
    record = {"key": entry.key, "owned_bytes": entry.owned_bytes}
    from ..pdf_visual_authoring import PdfVisualPlan, VERSION_V34, VERSION_V35, VERSION_V36, prompt_for_policy
    if entry.plan.policy_version in (VERSION_V34, VERSION_V35, VERSION_V36):
        # V34 is the original prose compiler plus literal/chart supplements,
        # not a natural-layout plan with a layout admission seal. Bind every
        # declared nested field/cache/pixel to the private entry HMAC instead.
        # This digest is freshly computed on every lookup, never memoized.
        from .pdf_authoring_source import _typed_plan_digest
        record["admitted_v34_plan_sha256" if entry.plan.policy_version == VERSION_V34
            else "admitted_v36_plan_sha256" if entry.plan.policy_version == VERSION_V36
            else "admitted_v35_plan_sha256"] = _typed_plan_digest(entry.plan,
            prompt_for_policy(entry.plan.policy_version), expected_type=PdfVisualPlan,
            maximum_hashed_bytes=335_544_320, maximum_text_bytes=268_435_456,
            deduplicate=True)
    else:
        record["admitted_plan_seal"] = entry.plan._layout_validation_seal
    if entry.inventory is not None:
        record["inventory"] = entry.inventory
    return hmac.digest(key, _json(record).encode(), "sha256").hex()


def _checked_reuse_plan(plan):
    from ..pdf_visual_authoring import PdfVisualPlan, VERSION_V34, VERSION_V35, VERSION_V36, _checked
    if type(plan) is PdfVisualPlan and plan.policy_version in (VERSION_V34, VERSION_V35, VERSION_V36):
        # Keep the existing complete reconstruction guard. Dataclass equality
        # omits these compiler caches; compare them explicitly before sealing
        # or accepting the original retained plan. No worker/model runs here.
        current = _checked(plan)
        if (plan.layout_source.source is not plan.source
                or plan.layout_source.native_figures is not None
                or plan.figure_rois is not None
                or any(getattr(plan, name) != getattr(current, name) for name in
                    ("_doc", "_catalog", "_tables", "_outline", "_topic_outline",
                     "_layout_plan", "_layout_validation_seal"))):
            raise PdfVisualPlanReuseConflict("Changed complete chart compiler plan.")
        return plan
    from ..pdf_layout_retention import checked_admitted_plan
    return checked_admitted_plan(plan)


def _checked_rows(plan, rows):
    """Only complete immutable primitive rows from an issued run may be cached."""
    from ..pdf_visual_authoring import VERSION_V31, VERSION_V32, VERSION_V35, VERSION_V36
    if plan.policy_version not in (VERSION_V31, VERSION_V32, VERSION_V35, VERSION_V36) or type(rows) is not tuple or len(rows) != len(plan.batches):
        raise PdfVisualPlanReuseConflict("Whole-inventory reuse requires the current ordinary writer.")
    for batch, row in zip(plan.batches, rows, strict=True):
        if (type(row) is not tuple or len(row) != 10
                or any(type(row[i]) is not int for i in (0, 4, 6, 7, 8, 9))
                or any(type(row[i]) is not str for i in (1, 2, 3, 5))
                or row[0] != batch.ordinal or row[1] != batch.batch_id
                or row[2] != _json([plan.chunks[i-1].chunk_id for i in batch.chunk_ordinals])
                or row[5] != _json(list(batch.chunk_ordinals))
                or row[6] != batch.page or row[7] != int(batch.visual)
                or len(row[3]) != 64 or any(c not in "0123456789abcdef" for c in row[3])
                or not 0 < row[8] <= plan.model_policy.request_budget_bytes
                or row[9] < 0 or row[4] != row[8] + row[9]):
            raise PdfVisualPlanReuseConflict("Changed complete PDF request inventory.")
    if not rows or len({row[1] for row in rows}) != len(rows):
        raise PdfVisualPlanReuseConflict("Incomplete PDF request inventory.")
    return rows


class _PdfVisualPlanReuse:
    def __init__(self, owner):
        self.owner = owner
        self._entry = None
        self._lock = Lock()
        self._key = secrets.token_bytes(32)

    def get(self, owner, key, *, geometry, pages, chart_supplement=None):
        if owner is not self.owner or type(key) is not tuple:
            raise PdfVisualPlanReuseConflict("Foreign PDF plan reuse scope.")
        with self._lock:
            entry = self._entry
        if entry is None:
            return None
        try:
            if type(entry) is _Entry and entry.inventory is not None:
                _checked_rows(entry.plan, entry.inventory)
            if (type(entry) is not _Entry or type(entry.owned_bytes) is not int
                    or not 0 < entry.owned_bytes <= MAXIMUM_OWNED_BYTES
                    or type(entry.seal) is not str
                    or not hmac.compare_digest(entry.seal, _entry_seal(entry, self._key))):
                raise ValueError("Changed reuse entry.")
            if entry.key != key:
                return None
            plan = _checked_reuse_plan(entry.plan)
            # Compare fresh verified actual bytes/attributes, not archived
            # caller-controlled hashes or transplanted source capability seals.
            if (type(geometry) is not bytes
                    or plan.source.geometry.geometry_json != geometry
                    or type(pages) is not tuple or len(pages) != len(plan.source.pages)
                    or any((old.page, old.width, old.height, old.image.source_id, old.image.media_type,
                            old.image.data) !=
                           (new.page, new.width, new.height, new.image.source_id, new.image.media_type,
                            new.image.data)
                           for old, new in zip(plan.source.pages, pages, strict=True))):
                raise ValueError("Changed retained PDF pixels or geometry.")
            from ..pdf_visual_authoring import VERSION_V34, VERSION_V35, VERSION_V36
            if plan.policy_version in (VERSION_V34, VERSION_V35, VERSION_V36) and (
                    type(chart_supplement) is not bytes
                    or plan.chart_supplement.metadata_json.encode("utf-8") != chart_supplement):
                raise ValueError("Changed complete retained chart bytes.")
            return plan
        except (ValueError, TypeError, AttributeError, UnicodeError, RecursionError) as exc:
            raise PdfVisualPlanReuseConflict("Prepared PDF plan reuse failed its private source check.") from exc

    def _checked_inventory(self, owner, key, plan):
        if owner is not self.owner or type(key) is not tuple:
            raise PdfVisualPlanReuseConflict("Foreign PDF inventory reuse scope.")
        with self._lock:
            entry = self._entry
        if entry is None:
            return None
        if type(entry) is _Entry and entry.inventory is not None:
            _checked_rows(entry.plan, entry.inventory)
        if (type(entry) is not _Entry or type(entry.owned_bytes) is not int
                or not 0 < entry.owned_bytes <= MAXIMUM_OWNED_BYTES
                or type(entry.seal) is not str
                or not hmac.compare_digest(entry.seal, _entry_seal(entry, self._key))):
            raise PdfVisualPlanReuseConflict("Changed PDF inventory reuse entry.")
        if entry.key != key or entry.plan is not plan or entry.inventory is None:
            return None
        from ..pdf_visual_authoring import VERSION_V35, VERSION_V36
        if plan.policy_version in (VERSION_V35, VERSION_V36):
            _checked_reuse_plan(plan)
        else:
            from ..pdf_layout_retention import checked_admitted_plan
            checked_admitted_plan(plan)
        return _checked_rows(plan, entry.inventory)

    def put(self, owner, key, plan, *, run=None):
        if owner is not self.owner or type(key) is not tuple:
            raise PdfVisualPlanReuseConflict("Foreign PDF plan reuse scope.")
        from ..pdf_visual_authoring import PdfVisualPlan, VERSION_V12, VERSION_V13, VERSION_V14, VERSION_V15, VERSION_V16, VERSION_V17, VERSION_V18, VERSION_V19, VERSION_V20, VERSION_V21, VERSION_V22, VERSION_V23, VERSION_V24
        from ..pdf_visual_authoring import VERSION_V25, VERSION_V26, VERSION_V27, VERSION_V28, VERSION_V29, VERSION_V30, VERSION_V31, VERSION_V32, VERSION_V34, VERSION_V35, VERSION_V36
        if type(plan) is not PdfVisualPlan or plan.policy_version not in (VERSION_V12,VERSION_V13,VERSION_V14,VERSION_V15, VERSION_V16, VERSION_V17, VERSION_V18, VERSION_V19, VERSION_V20, VERSION_V21, VERSION_V22, VERSION_V23, VERSION_V24, VERSION_V25, VERSION_V26, VERSION_V27, VERSION_V28, VERSION_V29, VERSION_V30, VERSION_V31, VERSION_V32, VERSION_V34, VERSION_V35, VERSION_V36):
            return False
        _checked_reuse_plan(plan)
        inventory = None
        if run is not None:
            from ..repositories.pdf_visual_authoring import PreparedVisualRun, _verify
            if (plan.policy_version not in (VERSION_V31, VERSION_V32, VERSION_V35, VERSION_V36) or type(run) is not PreparedVisualRun
                    or run.source.plan is not plan or run.source._leases.owner is not owner
                    or run.source._leases.token not in owner._active):
                raise PdfVisualPlanReuseConflict("Foreign issued PDF inventory run.")
            try:
                _verify("run", (run.identity(), run.batches), run.seal)
            except (ValueError, TypeError, AttributeError, UnicodeError, RecursionError) as exc:
                raise PdfVisualPlanReuseConflict("PDF inventory was not issued by the current run preparation.") from exc
            inventory = _checked_rows(plan, run.batches)
        try:
            size = _owned_bytes((key, plan) if inventory is None else (key, plan, inventory))
        except (ValueError, TypeError, AttributeError, RecursionError):
            with self._lock:
                self._entry = None
            return False
        entry = _Entry(key, plan, size, "", inventory)
        entry = _Entry(key, plan, size, _entry_seal(entry, self._key), inventory)
        with self._lock:
            self._entry = entry
        return True


def _reuse_key(service, before, saved, original, retained):
    """Actual freshly loaded raw bytes, and exact current archive/scope keys."""
    if (type(original.content) is not bytes
            or len(original.content) != before.source["raw_size_bytes"]
            or sha256(original.content).hexdigest() != before.source["raw_sha256"]):
        raise PdfVisualPlanReuseConflict("Current original PDF bytes differ.")
    return (before.preview["id"], before.source["publisher_user_id"], before.fence_sha256,
        saved.metadata, saved.bundle_json, original.original_filename,
        before.source["raw_sha256"], before.source["raw_size_bytes"], retained, service._python)


def _seed_committed_preparation(service, archive, prepared, staged, *, check_committed, cancel_event=None, run=None):
    """Seed selected live initial capabilities after fresh committed-archive reads.

    This does not restore/re-extract/render a source or retain its lease, run or
    success authority. The existing restore path must still perform every fresh
    scope/archive/raw/asset/configuration/cancellation check before a cache hit.
    The callback is a workflow-owned read-only succeeded-run guard, not a bool
    supplied by a caller that could treat cleanup as evidence of commit.
    """
    from ..pdf_visual_authoring import VERSION_V30, VERSION_V31, VERSION_V32, VERSION_V35, VERSION_V36
    from ..repositories.pdf_visual_archive import (
        PdfVisualArchiveRepository, PreparedPdfVisualArchive, ArchivedPdfVisualBytes,
        _seal as archive_seal, _cancel, _close_all,
    )
    from .pdf_visual_authoring_source import CanonicalPdfVisualSourceService, PreparedCanonicalPdfVisualSource
    from .pdf_visual_authoring_restore import _canonical_object, _configuration

    if (type(prepared) is not PreparedCanonicalPdfVisualSource
            or prepared.plan.policy_version not in (VERSION_V30, VERSION_V31, VERSION_V32, VERSION_V35, VERSION_V36)
            or (prepared.plan.policy_version in (VERSION_V35, VERSION_V36) and run is None)):
        return False
    if "protected_binding" in _canonical_object(prepared.configuration_json):
        return False  # Historical/protected preparation performs no seed I/O.
    if (type(service) is not CanonicalPdfVisualSourceService
            or type(archive) is not PdfVisualArchiveRepository
            or archive.source_service is not service or archive.stager is not service._stager
            or type(staged) is not PreparedPdfVisualArchive or staged.source is not prepared
            or staged._leases.owner is not archive or staged._leases.token not in archive._active
            or type(staged.seal) is not str or not hmac.compare_digest(staged.seal, archive_seal(staged))
            or not callable(check_committed)):
        raise PdfVisualPlanReuseConflict("Initial PDF preparation or archive scope differs.")
    check_committed()
    _cancel(cancel_event)
    archive._source_current(prepared)  # Official live capability + fresh RO source seal/authority.
    before = service._read(prepared.preview_id, prepared.actor_user_id, now_ms=0)
    if before.source_extension is not None:
        return False  # Never seed a protected derivative, even under the new policy.
    if (before.fence_sha256 != prepared.fence_sha256
            or before.preview["durable_job_id"] != prepared.parent_job_id
            or before.source["id"] != prepared.artifact_id):
        raise PdfVisualPlanReuseConflict("Initial PDF preparation no longer belongs to its source.")
    leases = []
    try:
        saved = archive.read(preview_id=prepared.preview_id, actor_user_id=prepared.actor_user_id,
            cancel_event=cancel_event)
        leases.append(saved)
        if (type(saved) is not ArchivedPdfVisualBytes or saved._leases.owner is not archive
                or saved._leases.token not in archive._active
                or saved.metadata != staged.metadata or saved.bundle_json != staged.bundle_json):
            raise PdfVisualPlanReuseConflict("Initial archive was not committed exactly.")
        bundle = _canonical_object(saved.bundle_json)
        config, model, work, execution = _configuration(service, bundle, None)
        retained = _json([{key: row[key] for key in
            ("id", "ordinal", "logical_name", "media_type", "content_sha256", "size_bytes")}
            for row in before.assets])
        plan = prepared.plan
        if (config["policy_version"] != plan.policy_version
                or bundle["configuration_json"] != prepared.configuration_json
                or bundle["retained_assets_json"] != retained or retained != prepared.retained_assets_json
                or bundle["plan_sha256"] != plan.plan_sha256
                or _json(bundle["source"]) != _json(plan.source.identity())
                or config["chunk_bytes"] != plan.chunk_bytes
                or model != plan.model_policy or work != plan.work_policy or execution != plan.execution_policy
                or saved.geometry != plan.source.geometry.geometry_json
                or len(saved.pages) != len(plan.source.pages)
                or any((old.page, old.width, old.height, old.image.source_id, old.image.media_type, old.image.data)
                    != (new.page, new.width, new.height, new.image.source_id, new.image.media_type, new.image.data)
                    for old, new in zip(plan.source.pages, saved.pages, strict=True))):
            raise PdfVisualPlanReuseConflict("Committed PDF bytes/configuration differ from initial preparation.")
        if plan.policy_version in (VERSION_V35, VERSION_V36) and (
                plan.source_definitions is not (plan.policy_version == VERSION_V35)
                or _json(config["layout_identity"]) != _json(plan.layout_source.identity())
                or _json(config["chart_identity"]) != _json(plan.chart_supplement.identity(cancel_event=cancel_event))
                or type(saved.chart_supplement) is not bytes
                or saved.chart_supplement != plan.chart_supplement.metadata_json.encode("utf-8")):
            raise PdfVisualPlanReuseConflict("Committed V35 layout/chart source differs from initial preparation.")
        _cancel(cancel_event)
        original = service._loader.load(artifact_id=before.source["id"],
            expected_sha256=before.source["raw_sha256"], expected_size=before.source["raw_size_bytes"],
            expected_media_type=before.source["media_type"])
        leases.append(original.lease)
        if original.original_filename != before.source["original_filename"]:
            raise PdfVisualPlanReuseConflict("Initial PDF source filename changed.")
        for asset in before.assets:
            _cancel(cancel_event)
            service._verify_retained_asset(asset, leases)
        _cancel(cancel_event)
        key = _reuse_key(service, before, saved, original, retained)
        archive._source_current(prepared)  # Recheck original live capability, never reseal it.
        if (service._read(prepared.preview_id, prepared.actor_user_id, now_ms=0).fence_sha256
                != before.fence_sha256
                or archive._load(prepared.preview_id, prepared.actor_user_id)
                    != (staged.metadata, staged.bundle_json, staged.pages)):
            raise PdfVisualPlanReuseConflict("PDF source/archive changed before reuse seed.")
        check_committed()
        _cancel(cancel_event)
        reuse = service._visual_plan_reuse
        if type(reuse) is not _PdfVisualPlanReuse or reuse.owner is not service:
            raise PdfVisualPlanReuseConflict("Foreign PDF plan reuse owner.")
        if run is not None:
            from ..repositories.pdf_visual_authoring import PreparedVisualRun
            if (type(run) is not PreparedVisualRun or run.source is not prepared
                    or run.source_archive_id != staged.metadata[0]
                    or [row[3] for row in run.batches] != bundle["requests"]):
                raise PdfVisualPlanReuseConflict("Committed PDF run differs from its complete archive inventory.")
        return reuse.put(service, key, plan, run=run)
    finally:
        _close_all(leases)

"""Managed background processes for the isolated Second Brain preview.

The localhost preview deliberately keeps its web process in the development
role.  This module starts the same durable job and projection primitives in
separate child processes, so model/network work never runs inside a Flask
request and projection files remain replaceable outputs of the canonical DB.

Codex is an explicit local-validation lane.  Without Codex validation the
semantic judge is restricted to the frozen ``hynix_api`` lane name, which is
the application's provider-neutral OpenAI-compatible HTTP adapter.
"""

from __future__ import annotations

import argparse
from contextlib import closing
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import threading
import time
import traceback
from typing import Mapping

from code_assist_v1.knowledge_core.errors import KnowledgeCoreSchemaMismatchError


_OPENAI_COMPATIBLE = "openai-compatible"
_CODEX_VALIDATION = "codex-validation"
_KINDS = ("durable", "projector")
_DEFAULT_DURABLE_COUNT = 1
_DEFAULT_PROJECTOR_COUNT = 1
_SUPERVISOR_POLL_S = 0.5
_RESTART_WINDOW_S = 60.0
_MAX_RESTARTS_PER_WINDOW = 3
_MAX_RESTART_BACKOFF_S = 30.0


class PreviewWorkerError(RuntimeError):
    """Stable failure raised by the marker-bound preview worker supervisor."""


def _schema_mismatch_details(root: Path, kind: str, error: BaseException) -> dict[str, object]:
    """Capture failure-only, non-content schema facts without changing the failure."""

    from code_assist_v1.knowledge_core.migrations.fingerprint import schema_sha256

    details: dict[str, object] = {
        "event": "preview_worker_schema_mismatch",
        "kind": kind,
        "expected_sha256": getattr(error, "expected_schema_sha256", None),
        "observed_at_failure_sha256": getattr(error, "observed_schema_sha256", None),
    }
    database = root / "core" / "knowledge_core.db"
    try:
        with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=2.0)) as probe:
            details["post_failure_sha256"] = schema_sha256(probe)
            details["post_failure_user_version"] = probe.execute("PRAGMA user_version").fetchone()[0]
            details["post_failure_schema_version"] = probe.execute("PRAGMA schema_version").fetchone()[0]
    except Exception as diagnostic_error:
        details["post_failure_error"] = type(diagnostic_error).__name__
    return details


def _confirmed_transient_schema_mismatch(root: Path, error: BaseException) -> bool:
    """Reopen and validate the complete canonical schema before a 10521 retry.

    The failed connection is never reused.  A persistent or unclassified
    mismatch remains fatal; this does not relax ordinary connection checks.
    """

    expected = getattr(error, "expected_schema_sha256", None)
    observed = getattr(error, "observed_schema_sha256", None)
    if (not isinstance(expected, str) or len(expected) != 64
            or not isinstance(observed, str) or len(observed) != 64
            or observed == expected):
        return False
    try:
        factory = _preview_factory(root)
        catalog = factory.migration_catalog
        if catalog.migration(catalog.latest_version).expected_schema_sha256 != expected:
            return False
        # _preview_factory already opens and validates once.  A second fresh
        # handle prevents a single lucky observation from authorizing retry.
        probe = factory.open(read_only=True)
        probe.close()
    except Exception:
        return False
    return True


def _serve_10521_with_schema_reconfirmation(service, stop: threading.Event, root: Path) -> None:
    """Retry only reconfirmed transient schema observations, at a bounded rate."""

    recovery_times: list[float] = []
    while not stop.is_set():
        try:
            service.serve(stop)
            return
        except KnowledgeCoreSchemaMismatchError as error:
            now = time.monotonic()
            recovery_times = [at for at in recovery_times if now - at < 60.0]
            if (len(recovery_times) >= 3
                    or not _confirmed_transient_schema_mismatch(root, error)):
                raise
            recovery_times.append(now)
            print(json.dumps({
                "event": "preview_worker_schema_reconfirmed",
                "expected_sha256": error.expected_schema_sha256,
                "observed_at_failure_sha256": error.observed_schema_sha256,
                "recoveries_in_minute": len(recovery_times),
            }, sort_keys=True), file=sys.stderr, flush=True)
            if stop.wait(0.5):
                return


@dataclass(frozen=True, slots=True)
class PreviewSemanticProviderPolicy:
    mode: str
    provider_order: tuple[str, ...]
    model_ids: tuple[str, ...] | None
    model_provider: str
    model_name: str


def preview_semantic_provider_policy(
    *, codex_validation: bool
) -> PreviewSemanticProviderPolicy:
    """Select either Codex validation or OpenAI-compatible production shape."""

    if not isinstance(codex_validation, bool):
        raise TypeError("codex_validation must be Boolean.")
    if codex_validation:
        return PreviewSemanticProviderPolicy(
            mode=_CODEX_VALIDATION,
            provider_order=("codex",),
            model_ids=("codex-local",),
            model_provider="codex_bridge_validation",
            model_name="codex-local",
        )
    return PreviewSemanticProviderPolicy(
        mode=_OPENAI_COMPATIBLE,
        provider_order=("hynix_api",),
        model_ids=None,
        model_provider="openai_compatible",
        model_name="configured-openai-compatible-model",
    )


def _atomic_json(path: Path, value: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(
            dict(value),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _parent_alive(parent_pid: int) -> bool:
    if parent_pid < 1:
        return False
    if sys.platform == "win32":
        import ctypes

        synchronize = 0x00100000
        wait_timeout = 0x00000102
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        open_process = kernel32.OpenProcess
        open_process.argtypes = (ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32)
        open_process.restype = ctypes.c_void_p
        wait_for_single_object = kernel32.WaitForSingleObject
        wait_for_single_object.argtypes = (ctypes.c_void_p, ctypes.c_uint32)
        wait_for_single_object.restype = ctypes.c_uint32
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = (ctypes.c_void_p,)
        close_handle.restype = ctypes.c_int
        handle = open_process(synchronize, 0, parent_pid)
        if not handle:
            return False
        try:
            return int(wait_for_single_object(handle, 0)) == wait_timeout
        finally:
            close_handle(handle)
    try:
        os.kill(parent_pid, 0)
    except OSError:
        return False
    return True


def _watch_parent_and_stop_file(
    stop: threading.Event,
    *,
    parent_pid: int,
    stop_file: Path,
) -> None:
    while not stop.wait(0.25):
        if stop_file.is_file() or not _parent_alive(parent_pid):
            stop.set()
            return


def _preview_factory(root: Path, *, preservation_active=None):
    from code_assist_v1.tools.clean_10521_runtime import is_clean_root, catalog_for
    if is_clean_root(root):
        from code_assist_v1.knowledge_core.db import ConnectionFactory
        catalog = catalog_for(root)
        factory = ConnectionFactory(root / "core" / "knowledge_core.db", catalog)
        probe = factory.open(read_only=True)
        probe.close()
        return factory
    from code_assist_v1.tools.preservation_preview_runtime import enabled, factory_for
    from code_assist_v1.tools.native_publication_preview_runtime import native_publication_enabled
    native_active = native_publication_enabled(root)
    if preservation_active is None:
        preservation_active = enabled(root)
    if type(preservation_active) is not bool:
        raise TypeError("preservation_active must be boolean")
    if native_active and not preservation_active:
        raise PreviewWorkerError("Native and preservation activation disagree.")
    if preservation_active:
        return factory_for(root)
    from code_assist_v1.knowledge_core.db import ConnectionFactory
    from code_assist_v1.knowledge_core.migrations import CANONICAL_MIGRATION_CATALOG

    database = root / "core" / "knowledge_core.db"
    if not database.is_file():
        raise PreviewWorkerError("The preview canonical database is unavailable.")
    factory = ConnectionFactory._for_isolated_tests(
        database, CANONICAL_MIGRATION_CATALOG
    )
    probe = factory.open(read_only=True)
    probe.close()
    return factory


def _storage_layout(root: Path):
    from code_assist_v1.knowledge_core.migrations import MigrationStorageLayout

    return MigrationStorageLayout(
        data_root=root,
        database=root / "core" / "knowledge_core.db",
        blob_root=root / "blobs",
        projection_root=root / "projections",
    )


def _compose_durable_worker(
    root: Path,
    *,
    codex_validation: bool,
    worker_index: int = 0,
    reuse_preparation_reads: bool = False,
    reuse_maintenance_reads: bool = False,
):
    from code_assist_v1.knowledge_core.inference_worker import (
        DurableInferenceWorker,
        DurableInferenceWorkerConfig,
    )
    from code_assist_v1.knowledge_core.legacy.blob_stager import (
        FilesystemLegacyBlobStager,
    )
    from code_assist_v1.knowledge_core.scope import ScopeResolver
    from code_assist_v1.knowledge_core.services.automatic_semantic_edges import (
        AutomaticSemanticEdgeHandler,
        InternalLLMSemanticJudge,
    )
    from code_assist_v1.knowledge_core.services.curation_worker import (
        CanonicalCurationRescanHandler,
    )
    from code_assist_v1.knowledge_core.services.publication import (
        CanonicalPublicationSourceLoader,
        PublicationPreviewExtractionHandler,
    )
    from code_assist_v1.knowledge_core.services.rich_publication import (
        CanonicalRichPublicationExtractor,
    )
    from code_assist_v1.knowledge_core.services.semantic_edge_seeding import (
        SemanticIntentMaterializer,
        SemanticIntentSeedService,
        SemanticSeedTriggerConsumer,
    )
    from code_assist_v1.knowledge_core.worker import DurableWorkKernel, HandlerRegistry
    from code_assist_v1.tools.full_second_brain_preview import (
        _preview_feature_flags,
    )

    if (
        isinstance(worker_index, bool)
        or not isinstance(worker_index, int)
        or not 0 <= worker_index <= 15
    ):
        raise ValueError("worker_index must be between zero and fifteen.")
    if type(reuse_preparation_reads) is not bool:
        raise TypeError("Preparation read reuse must be an explicit boolean.")
    if type(reuse_maintenance_reads) is not bool:
        raise TypeError("Maintenance read reuse must be an explicit boolean.")
    from code_assist_v1.tools.preservation_preview_runtime import enabled, workflow_for, extractor_for
    preservation_active = enabled(root)
    factory = _preview_factory(root, preservation_active=preservation_active)
    preparation_binding = None
    if reuse_preparation_reads:
        from code_assist_v1.knowledge_core.preparation_reads import PreparationReadBinding
        preparation_binding = PreparationReadBinding(factory)
    layout = _storage_layout(root)
    flags = _preview_feature_flags()
    scope_resolver = ScopeResolver(flags)
    stager = FilesystemLegacyBlobStager(layout)
    publication = PublicationPreviewExtractionHandler(
        factory,
        CanonicalPublicationSourceLoader(factory, stager),
        extractor_for(root),
        asset_stager=stager,
    )
    curation = CanonicalCurationRescanHandler(
        factory,
        scope_resolver=scope_resolver,
    )
    policy = preview_semantic_provider_policy(
        codex_validation=codex_validation
    )
    semantic = AutomaticSemanticEdgeHandler(
        factory,
        InternalLLMSemanticJudge(
            provider_order=policy.provider_order,
            model_ids=policy.model_ids,
        ),
        model_provider=policy.model_provider,
        model_name=policy.model_name,
    )
    from code_assist_v1.tools.native_publication_preview_runtime import native_mode_for_factory, composition_for
    preservation = None
    native_active = native_mode_for_factory(factory, root)
    if native_active:
        from code_assist_v1.tools.clean_10521_runtime import is_clean_root
        from code_assist_v1.tools.protected_source_runtime import (
            approved_protected_provider_for_10521_runtime,
        )
        protected_provider = (approved_protected_provider_for_10521_runtime()
            if is_clean_root(root) else None)
        kernel = composition_for(factory, scope_resolver, stager,
            root=root, codex_validation=codex_validation, worker=True,
            protected_source_opener=protected_provider,
            policy_protection_classifier=(
                protected_provider if (protected_provider is not None
                    and factory.migration_catalog.latest_version >= 60
                    and callable(getattr(protected_provider, "classify_for_actor", None)))
                else None),
            protected_publication_authorizer=(
                protected_provider.authorize_publication if protected_provider else None),
            additional_handlers=(curation.registered_handler(), *semantic.registered_handlers()),
            preparation_read_binding=preparation_binding).kernel
    else:
        preservation = workflow_for(factory, codex_validation=codex_validation) if preservation_active else None
        kernel = DurableWorkKernel(
            factory,
            HandlerRegistry(
                (
                    publication.registered_handler(),
                    curation.registered_handler(),
                    *semantic.registered_handlers(),
                    *(preservation.registered_handlers() if preservation is not None else ()),
                )
            ),
            scope_resolver=scope_resolver,
            preparation_read_binding=preparation_binding,
        )
    seeds = SemanticIntentSeedService(factory)
    if preservation is not None:
        preservation.bind_kernel(kernel)
    seeds.assert_baseline_complete()
    triggers = SemanticSeedTriggerConsumer(factory, seeds=seeds)
    materializer = SemanticIntentMaterializer(factory, kernel, flags)

    def semantic_maintenance() -> None:
        from code_assist_v1.knowledge_core.semantic_maintenance_reads import run_semantic_maintenance
        run_semantic_maintenance(triggers, seeds, materializer,
            reuse_reads=reuse_maintenance_reads)

    marker = _load_bound_marker(root)
    owner_suffix = str(marker["marker_sha256"])[:16]
    return DurableInferenceWorker(
        kernel,
        DurableInferenceWorkerConfig(
            claim_owner=f"full-preview-{owner_suffix}:durable:{worker_index:02d}",
            # The isolated 10421 clone has only Codex/HTTP semantic providers.
            # It must coexist with the unchanged rollback server while never
            # gaining permission to load a local GGUF model.
            ownership_mode=("remote_only" if marker.get("clone_port") in (10421, 10521, 10541) else "local_gguf"),
            # Explicitly limited to the validated native preview composition;
            # ordinary/legacy workers keep their separate physical connections.
            revalidate_poll_connection=(native_active and marker.get("clone_port") in (10421, 10521)),
        ),
        before_poll=semantic_maintenance,
    )


def _compose_projection_worker(root: Path, *, worker_index: int = 0):
    from code_assist_v1.knowledge_core.projection.worker import (
        OutboxProjectionWorker,
        ProjectionWorkerConfig,
    )
    from code_assist_v1.tools.full_second_brain_preview import (
        _preview_feature_flags,
    )

    if (
        isinstance(worker_index, bool)
        or not isinstance(worker_index, int)
        or not 0 <= worker_index <= 15
    ):
        raise ValueError("worker_index must be between zero and fifteen.")
    marker = _load_bound_marker(root)
    owner_suffix = str(marker["marker_sha256"])[:16]
    flags = _preview_feature_flags()
    return OutboxProjectionWorker(
        _preview_factory(root),
        _storage_layout(root),
        ProjectionWorkerConfig(
            claim_owner=f"full-preview-{owner_suffix}:projector:{worker_index:02d}",
            footer_v2=flags.projection_footer_v2,
            # Aggregate projections share one global physical target.  Keep
            # that lane on projector zero while the remaining workers rebuild
            # distinct per-space Markdown targets in parallel.
            aggregate_graphs=flags.aggregate_graph_reads and worker_index == 0,
            maximum_attempts=20,
            settle_space_batch_size=512,
        ),
    )


def _load_bound_marker(root: Path) -> dict[str, object]:
    from code_assist_v1.tools.clean_10521_runtime import is_clean_root, selection_for
    if is_clean_root(root):
        selected = selection_for(root)
        from code_assist_v1.tools.recovered_preview_binding import load_binding
        binding = load_binding(root)
        marker = {
            "marker_sha256": selected.activation_sha256,
            "data_root": str(root.resolve(strict=True)),
            "clone_port": 10521 if binding is None else binding.port,
        }
        from code_assist_v1.knowledge_core.pdf_visual_authoring import VERSION_V34, VERSION_V35, VERSION_V36
        if getattr(selected, 'policy_version', None) in (VERSION_V34, VERSION_V35, VERSION_V36):
            marker['chart_preparation'] = json.loads(selected.upload_policy().encoded())['configuration']['chart_preparation']
        return marker
    from code_assist_v1.tools.full_second_brain_preview import _load_marker

    return dict(_load_marker(root))


def _normal_10531_read_reuse(root: Path, marker: Mapping[str, object], *,
                             codex_validation: bool) -> bool:
    """Opt in only the verified ordinary V90–V93 HTTP clone, never other lanes.

    The existing read lifetimes cache no authorization/query results. Their
    fresh runtime/schema/authority checks remain mandatory on every borrow.
    Re-read the official activation/binding rather than trusting a port label.
    """
    if marker.get("clone_port") != 10531 or codex_validation:
        return False
    from code_assist_v1.tools.clean_10521_runtime import is_clean_root, selection_for
    from code_assist_v1.tools.recovered_preview_binding import _CLEAN_RECOVERY_KIND, load_binding
    from code_assist_v1.knowledge_core.pdf_visual_authoring import VERSION_V31, VERSION_V32, VERSION_V33, VERSION_V34, VERSION_V35, VERSION_V36

    if not is_clean_root(root):
        return False
    selected = selection_for(root)
    binding = load_binding(root)
    if (selected is None or binding is None
            or binding.kind != _CLEAN_RECOVERY_KIND or binding.port != 10531
            or binding.provider_mode != _OPENAI_COMPATIBLE
            or binding.data_root != root.resolve(strict=True)
            or selected.root != binding.data_root
            or marker.get("data_root") != str(binding.data_root)
            or marker.get("marker_sha256") != selected.activation_sha256):
        raise PreviewWorkerError("The normal 10531 read-reuse binding changed.")
    if selected.policy_version in (VERSION_V34, VERSION_V35, VERSION_V36):
        profile = json.loads(selected.upload_policy().encoded())['configuration']['chart_preparation']
        if marker.get('chart_preparation') != profile:
            raise PreviewWorkerError("The normal 10531 chart-preparation profile changed.")
    return selected.policy_version in (VERSION_V31, VERSION_V32, VERSION_V33, VERSION_V34, VERSION_V35, VERSION_V36) and selected.provider == "hynix_api"


def _normal_10541_v97_read_reuse(root: Path, marker: Mapping[str, object], *,
                                codex_validation: bool) -> bool:
    """Reuse existing fresh read lifetimes only on the exact A10541 V97 lane.

    Selection and binding validation retain the complete V95/V96/V97 chain.
    This changes no per-borrow catalog/authority/transaction checks, source
    selection, parser policy, protected-source callbacks or poll connection.
    """
    if marker.get("clone_port") != 10541 or codex_validation:
        return False
    from code_assist_v1.tools.clean_10521_runtime import is_clean_root, selection_for, catalog_for
    from code_assist_v1.tools.recovered_preview_binding import _CLEAN_RECOVERY_KIND, load_binding
    from code_assist_v1.knowledge_core.pdf_visual_authoring import VERSION_V36
    from code_assist_v1.knowledge_core.migrations.pdf_context_resume_catalog import (
        PDF_CONTEXT_RESUME_MIGRATION_CATALOG as V97_CATALOG,
    )

    if not is_clean_root(root):
        return False
    selected = selection_for(root)
    binding = load_binding(root)
    if (selected is None or binding is None
            or binding.kind != _CLEAN_RECOVERY_KIND or binding.port != 10541
            or binding.provider_mode != _OPENAI_COMPATIBLE
            or binding.data_root != root.resolve(strict=True)
            or selected.root != binding.data_root
            or marker.get("data_root") != str(binding.data_root)
            or marker.get("marker_sha256") != selected.activation_sha256
            or binding.current_marker_sha256 != selected.activation_sha256
            or selected.policy_version != VERSION_V36 or selected.provider != "hynix_api"
            or selected.forward_partition is not True or selected.forward_continuation is not True):
        raise PreviewWorkerError("The normal A10541 V97 read-reuse binding changed.")
    catalog = catalog_for(root)
    if (catalog is not V97_CATALOG or catalog.latest_version != 97
            or binding.current_schema_catalog_checksum != catalog.checksum_sha256):
        raise PreviewWorkerError("The normal A10541 V97 read-reuse catalog changed.")
    profile = json.loads(selected.upload_policy().encoded())["configuration"]["chart_preparation"]
    if marker.get("chart_preparation") != profile:
        raise PreviewWorkerError("The normal A10541 V97 chart-preparation profile changed.")
    return True


def _normal_10541_v98_read_reuse(root: Path, marker: Mapping[str, object], *,
                                codex_validation: bool) -> bool:
    """Use the existing fresh read lifetimes only after exact V98 admission.

    The schema4 binding validates the immutable V95/V96/V97 predecessors;
    this does not weaken per-borrow authority, parser, or transaction checks.
    """
    if marker.get("clone_port") != 10541 or codex_validation:
        return False
    from code_assist_v1.tools.clean_10521_runtime import is_clean_root, selection_for, catalog_for
    from code_assist_v1.tools.recovered_preview_binding import _CLEAN_RECOVERY_KIND, load_binding
    from code_assist_v1.tools.pdf_terminal_result_reuse_activation import require_terminal_result_reuse_binding
    from code_assist_v1.knowledge_core.pdf_visual_authoring import VERSION_V36
    from code_assist_v1.knowledge_core.migrations.pdf_terminal_result_reuse_catalog import (
        PDF_TERMINAL_RESULT_REUSE_MIGRATION_CATALOG as V98_CATALOG,
    )

    if not is_clean_root(root):
        return False
    selected = selection_for(root)
    binding = load_binding(root)
    if (selected is None or binding is None
            or binding.kind != _CLEAN_RECOVERY_KIND or binding.port != 10541
            or binding.provider_mode != _OPENAI_COMPATIBLE
            or binding.data_root != root.resolve(strict=True)
            or selected.root != binding.data_root
            or marker.get("data_root") != str(binding.data_root)
            or marker.get("marker_sha256") != selected.activation_sha256
            or binding.current_marker_sha256 != selected.activation_sha256
            or selected.policy_version != VERSION_V36 or selected.provider != "hynix_api"
            or selected.forward_partition is not True or selected.forward_continuation is not True):
        raise PreviewWorkerError("The normal A10541 V98 read-reuse binding changed.")
    catalog = catalog_for(root)
    if (catalog is not V98_CATALOG or catalog.latest_version != 98
            or binding.current_schema_catalog_checksum != catalog.checksum_sha256):
        raise PreviewWorkerError("The normal A10541 V98 read-reuse catalog changed.")
    require_terminal_result_reuse_binding(root, binding=binding)
    profile = json.loads(selected.upload_policy().encoded())["configuration"]["chart_preparation"]
    if marker.get("chart_preparation") != profile:
        raise PreviewWorkerError("The normal A10541 V98 chart-preparation profile changed.")
    return True


def _normal_10541_read_reuse(root: Path, marker: Mapping[str, object], *,
                            codex_validation: bool) -> bool:
    """Dispatch the exact V98 lane; never catch or downgrade either guard."""
    if marker.get("clone_port") != 10541 or codex_validation:
        return False
    from code_assist_v1.tools.clean_10521_runtime import is_clean_root, catalog_for
    from code_assist_v1.knowledge_core.migrations.pdf_terminal_result_reuse_catalog import (
        PDF_TERMINAL_RESULT_REUSE_MIGRATION_CATALOG as V98_CATALOG,
    )
    if is_clean_root(root) and catalog_for(root) is V98_CATALOG:
        return _normal_10541_v98_read_reuse(root, marker, codex_validation=codex_validation)
    return _normal_10541_v97_read_reuse(root, marker, codex_validation=codex_validation)


def _run_child(args: argparse.Namespace) -> int:
    root = args.data_root.resolve(strict=True)
    marker = _load_bound_marker(root)
    if marker.get("marker_sha256") != args.marker_sha256:
        raise PreviewWorkerError("The child worker marker binding changed.")
    expected_mode = _CODEX_VALIDATION if args.codex_validation else _OPENAI_COMPATIBLE
    if args.provider_mode != expected_mode:
        raise PreviewWorkerError("The child worker provider policy is inconsistent.")
    normal_read_reuse = (args.kind == "durable" and (
        _normal_10531_read_reuse(root, marker, codex_validation=args.codex_validation)
        or _normal_10541_read_reuse(root, marker, codex_validation=args.codex_validation)))

    if marker.get("clone_port") in (10521, 10531, 10541) and args.kind == "durable":
        # Keep public/persisted errors generic.  Only this isolated worker's
        # private log receives non-content exception class/source coordinates.
        os.environ["CODE_ASSIST_10521_JOB_FAILURE_DIAGNOSTICS"] = "1"

    stop = threading.Event()
    for signal_name in ("SIGINT", "SIGTERM"):
        candidate = getattr(signal, signal_name, None)
        if candidate is not None:
            signal.signal(candidate, lambda *_ignored: stop.set())
    watcher = threading.Thread(
        target=_watch_parent_and_stop_file,
        kwargs={
            "stop": stop,
            "parent_pid": args.parent_pid,
            "stop_file": args.stop_file,
        },
        name=f"full-preview-{args.kind}-parent-watch",
        daemon=True,
    )
    watcher.start()

    if args.kind == "durable":
        service = _compose_durable_worker(
            root,
            codex_validation=args.codex_validation,
            worker_index=args.worker_index,
            reuse_preparation_reads=normal_read_reuse,
            reuse_maintenance_reads=normal_read_reuse,
        )
    else:
        service = _compose_projection_worker(root, worker_index=args.worker_index)
    _atomic_json(
        args.ready_file,
        {
            "kind": args.kind,
            "marker_sha256": args.marker_sha256,
            "pid": os.getpid(),
            "provider_mode": args.provider_mode,
            "schema_version": 1,
            "status": "ready",
            "worker_index": args.worker_index,
        },
    )
    if marker.get("clone_port") in (10521, 10531):
        _serve_10521_with_schema_reconfirmation(service, stop, root)
    else:
        service.serve(stop)
    return 0


@dataclass(slots=True)
class _ManagedChild:
    kind: str
    worker_index: int
    process: subprocess.Popen[str]
    log_path: Path
    ready_path: Path
    log_handle: object
    ready: bool = False
    ready_deadline: float = 0.0
    restart_count: int = 0
    restart_times: list[float] = field(default_factory=list)
    next_restart_at: float | None = None
    last_exit_code: int | None = None
    last_failure: str | None = None


class PreviewWorkerSupervisor:
    """Own one durable lane and bounded projection lanes for one preview host."""

    def __init__(
        self,
        root: Path,
        *,
        marker_sha256: str,
        codex_validation: bool,
        durable_count: int = _DEFAULT_DURABLE_COUNT,
        projector_count: int = _DEFAULT_PROJECTOR_COUNT,
    ) -> None:
        resolved = root.resolve(strict=True)
        marker = _load_bound_marker(resolved)
        if marker.get("marker_sha256") != marker_sha256:
            raise PreviewWorkerError("The supervisor marker binding is invalid.")
        self._root = resolved
        self._marker_sha256 = marker_sha256
        self._policy = preview_semantic_provider_policy(
            codex_validation=codex_validation
        )
        if (
            isinstance(durable_count, bool)
            or not isinstance(durable_count, int)
            or durable_count != 1
        ):
            raise ValueError(
                "durable_count must be one because the inference scheduler has a single owner."
            )
        if (
            isinstance(projector_count, bool)
            or not isinstance(projector_count, int)
            or not 1 <= projector_count <= 16
        ):
            raise ValueError("projector_count must be between one and sixteen.")
        self._codex_validation = codex_validation
        self._durable_count = durable_count
        self._projector_count = projector_count
        self._children: list[_ManagedChild] = []
        run_id = f"{os.getpid()}-{marker_sha256[:12]}"
        worker_root = resolved / ".runtime" / "full-preview-workers"
        self._stop_file = worker_root / f"{run_id}.stop"
        self._run_id = run_id
        # Only the explicitly bound A comparison host opts in.  Historical
        # preview hosts and the B comparison keep their existing lifecycle.
        self._automatic_recovery = marker.get("clone_port") == 10541
        self._clone_port = marker.get("clone_port")
        self._lock = threading.RLock()
        self._monitor_stop = threading.Event()
        self._monitor_thread: threading.Thread | None = None
        self._started = False
        self._stopping = False
        self._startup_timeout_s = 20.0
        self._monitor_failure: str | None = None

    @property
    def provider_mode(self) -> str:
        return self._policy.mode

    def start(self, *, timeout_s: float = 20.0) -> None:
        if timeout_s <= 0:
            raise ValueError("The worker readiness timeout must be positive.")
        with self._lock:
            if self._started or self._children:
                raise PreviewWorkerError("The preview worker supervisor is already started.")
            self._started = True
            self._stopping = False
            self._monitor_stop.clear()
            self._startup_timeout_s = timeout_s
            self._monitor_failure = None
        worker_root = self._stop_file.parent
        worker_root.mkdir(parents=True, exist_ok=True)
        self._stop_file.unlink(missing_ok=True)
        try:
            child_specs = tuple(
                ("durable", index) for index in range(self._durable_count)
            ) + tuple(
                ("projector", index) for index in range(self._projector_count)
            )
            for kind, worker_index in child_specs:
                with self._lock:
                    if self._monitor_stop.is_set():
                        raise PreviewWorkerError("The preview worker start was stopped.")
                    self._children.append(self._launch_child(kind, worker_index))
            deadline = time.monotonic() + timeout_s
            for child in tuple(self._children):
                while time.monotonic() < deadline:
                    if self._monitor_stop.is_set():
                        raise PreviewWorkerError("The preview worker start was stopped.")
                    return_code = child.process.poll()
                    if return_code is not None:
                        raise PreviewWorkerError(
                            f"The {child.kind} preview worker exited with {return_code}; "
                            f"see {child.log_path}."
                        )
                    if self._child_ready(child):
                        child.ready = True
                        break
                    time.sleep(0.05)
                else:
                    raise PreviewWorkerError(
                        f"The {child.kind} preview worker did not become ready; "
                        f"see {child.log_path}."
                    )
            time.sleep(0.25)
            failed = [child for child in self._children if child.process.poll() is not None]
            if failed:
                child = failed[0]
                raise PreviewWorkerError(
                    f"The {child.kind} preview worker stopped after readiness; "
                    f"see {child.log_path}."
                )
            if self._automatic_recovery:
                with self._lock:
                    if self._monitor_stop.is_set():
                        raise PreviewWorkerError("The preview worker start was stopped.")
                    self._monitor_thread = threading.Thread(
                        target=self._monitor_loop,
                        name=f"full-preview-{self._run_id}-supervisor",
                        daemon=True,
                    )
                    self._monitor_thread.start()
            elif self._monitor_stop.is_set():
                raise PreviewWorkerError("The preview worker start was stopped.")
        except BaseException:
            self.stop()
            raise

    def _launch_child(self, kind: str, worker_index: int) -> _ManagedChild:
        # Reconfirm the normal marker binding on every restart, not just at
        # web startup.  This never bypasses child schema/provider validation.
        marker = _load_bound_marker(self._root)
        if (marker.get("marker_sha256") != self._marker_sha256
                or marker.get("clone_port") != self._clone_port):
            raise PreviewWorkerError("The supervisor marker binding changed.")
        label = f"{kind}-{worker_index:02d}"
        worker_root = self._stop_file.parent
        ready_path = worker_root / f"{self._run_id}.{label}.ready.json"
        ready_path.unlink(missing_ok=True)
        log_path = worker_root / f"{self._run_id}.{label}.log"
        log_handle = log_path.open("a", encoding="utf-8")
        command = [
            sys.executable, "-m",
            "code_assist_v1.tools.full_second_brain_preview_workers",
            "--child", "--kind", kind,
            "--data-root", os.fspath(self._root),
            "--marker-sha256", self._marker_sha256,
            "--parent-pid", str(os.getpid()),
            "--provider-mode", self._policy.mode,
            "--worker-index", str(worker_index),
            "--ready-file", os.fspath(ready_path),
            "--stop-file", os.fspath(self._stop_file),
        ]
        if os.environ.get("CODE_ASSIST_LISTENER_PROMOTION_PORT") == "10521":
            # Embedded Python's ._pth intentionally ignores PYTHONPATH. Select
            # this restored package before the ordinary child entry point so
            # its existing code-root/recovery checks still apply unchanged.
            package_parent = os.fspath(Path(__file__).resolve().parents[2])
            child_boot = (
                "import sys,runpy;sys.path.insert(0," + repr(package_parent) + ");"
                "runpy.run_module('code_assist_v1.tools.full_second_brain_preview_workers',run_name='__main__')"
            )
            command = [sys.executable, "-B", "-c", child_boot, *command[3:]]
        if self._codex_validation:
            command.append("--codex-validation")
        environment = dict(os.environ)
        environment["CODE_ASSIST_PROCESS_ROLE"] = (
            "worker" if kind == "durable" else "projector"
        )
        environment["CODE_ASSIST_ENABLE_CODEX_BRIDGE"] = (
            "1" if self._codex_validation else "0"
        )
        try:
            process = subprocess.Popen(
                command,
                cwd=os.fspath(Path(__file__).resolve().parents[2]),
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                text=True,
                creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
            )
        except BaseException:
            log_handle.close()
            raise
        return _ManagedChild(
            kind, worker_index, process, log_path, ready_path, log_handle,
            ready_deadline=time.monotonic() + self._startup_timeout_s,
        )

    def _child_ready(self, child: _ManagedChild) -> bool:
        if child.process.poll() is not None or not child.ready_path.is_file():
            return False
        if not self._automatic_recovery:
            return True
        try:
            value = json.loads(child.ready_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        return (
            isinstance(value, dict)
            and type(value.get("pid")) is int
            and value["pid"] == child.process.pid
            and value.get("kind") == child.kind
            and type(value.get("worker_index")) is int
            and value["worker_index"] == child.worker_index
            and value.get("marker_sha256") == self._marker_sha256
            and value.get("provider_mode") == self._policy.mode
            and type(value.get("schema_version")) is int
            and value["schema_version"] == 1
            and value.get("status") == "ready"
        )

    def _recovery_event(self, child: _ManagedChild, event: str) -> None:
        # No exception text, document paths, payloads, or source contents.
        print(json.dumps({
            "event": event, "kind": child.kind,
            "worker_index": child.worker_index, "pid": child.process.pid,
            "exit_code": child.last_exit_code,
            "restart_count": child.restart_count,
            "failure": child.last_failure,
        }, sort_keys=True), file=sys.stderr, flush=True)

    def _monitor_loop(self) -> None:
        while not self._monitor_stop.wait(_SUPERVISOR_POLL_S):
            try:
                self._monitor_once()
            except Exception as error:
                # A filesystem/status diagnostic error must not silently kill
                # the watchdog or produce a busy restart loop.
                failure = type(error).__name__
                if self._monitor_failure != failure:
                    print(json.dumps({
                        "event": "preview_worker_supervisor_error",
                        "error_type": failure,
                    }, sort_keys=True), file=sys.stderr, flush=True)
                self._monitor_failure = failure

    def _monitor_once(self, *, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        with self._lock:
            if (not self._automatic_recovery or not self._started
                    or self._stopping or self._monitor_stop.is_set()):
                return
            if self._stop_file.is_file():
                self._stopping = True
                self._monitor_stop.set()
                return
            for index, child in enumerate(self._children):
                child.restart_times[:] = [
                    at for at in child.restart_times if now - at < _RESTART_WINDOW_S
                ]
                return_code = child.process.poll()
                if return_code is None:
                    if self._child_ready(child):
                        child.ready = True
                    elif not child.ready and now >= child.ready_deadline:
                        child.last_failure = "READINESS_TIMEOUT"
                        child.process.terminate()
                        # Wait for confirmed exit on the next tick before
                        # launching a replacement: never two scheduler owners.
                    continue
                if child.next_restart_at is None:
                    child.ready = False
                    child.last_exit_code = return_code
                    child.last_failure = child.last_failure or "UNEXPECTED_EXIT"
                    child.ready_path.unlink(missing_ok=True)
                    child.log_handle.close()
                    delay = min(
                        2.0 ** len(child.restart_times), _MAX_RESTART_BACKOFF_S
                    )
                    child.next_restart_at = now + delay
                    self._recovery_event(child, "preview_worker_exit_detected")
                if now < child.next_restart_at:
                    continue
                if len(child.restart_times) >= _MAX_RESTARTS_PER_WINDOW:
                    child.next_restart_at = child.restart_times[0] + _RESTART_WINDOW_S
                    child.last_failure = "RESTART_COOLDOWN"
                    self._recovery_event(child, "preview_worker_restart_cooldown")
                    continue
                if self._monitor_stop.is_set():
                    return
                child.restart_times.append(now)
                child.restart_count += 1
                try:
                    replacement = self._launch_child(child.kind, child.worker_index)
                except Exception as error:
                    child.last_failure = "START_FAILED:" + type(error).__name__
                    child.next_restart_at = now + min(
                        2.0 ** len(child.restart_times), _MAX_RESTART_BACKOFF_S
                    )
                    self._recovery_event(child, "preview_worker_restart_failed")
                    continue
                replacement.restart_times = child.restart_times
                replacement.restart_count = child.restart_count
                replacement.last_exit_code = child.last_exit_code
                self._children[index] = replacement
                self._recovery_event(replacement, "preview_worker_restarted")
            self._monitor_failure = None

    def status(self) -> dict[str, object]:
        with self._lock:
            children = [
                {
                    "alive": child.process.poll() is None,
                    "ready": self._child_ready(child),
                    "kind": child.kind,
                    "log_path": os.fspath(child.log_path),
                    "pid": child.process.pid,
                    "worker_index": child.worker_index,
                    "restart_count": child.restart_count,
                    "last_exit_code": child.last_exit_code,
                    "last_failure": child.last_failure,
                    "restart_in_s": (None if child.next_restart_at is None else
                        max(0.0, child.next_restart_at - time.monotonic())),
                }
                for child in self._children
            ]
            monitoring = self._monitor_thread is not None and self._monitor_thread.is_alive()
            healthy = bool(children) and all(child["ready"] for child in children)
            stopping = self._stopping or (self._started and self._monitor_stop.is_set())
            return {
                "children": children,
                "provider_mode": self._policy.mode,
                "schema_version": 1,
                "automatic_recovery": self._automatic_recovery,
                "monitoring": monitoring,
                "monitor_failure": self._monitor_failure,
                "status": ("stopping" if stopping else
                    "stopped" if not self._started else
                    "healthy" if healthy and (monitoring or not self._automatic_recovery)
                    else "degraded"),
            }

    def stop(self, *, timeout_s: float = 20.0) -> None:
        # Set before taking the lock so a concurrent watchdog cannot schedule
        # another replacement while shutdown is waiting for the lock.
        self._monitor_stop.set()
        monitor = self._monitor_thread
        if monitor is not None and monitor is not threading.current_thread():
            monitor.join(timeout=max(0.0, timeout_s))
        with self._lock:
            self._stopping = True
            if not self._children:
                self._started = False
                self._stopping = False
                self._monitor_thread = None
                return
            self._stop_children(timeout_s=timeout_s)

    def _stop_children(self, *, timeout_s: float) -> None:
        try:
            self._stop_file.parent.mkdir(parents=True, exist_ok=True)
            self._stop_file.write_text("stop\n", encoding="ascii")
            deadline = time.monotonic() + timeout_s
            for child in self._children:
                remaining = max(0.0, deadline - time.monotonic())
                try:
                    child.process.wait(timeout=remaining)
                except subprocess.TimeoutExpired:
                    child.process.terminate()
            for child in self._children:
                if child.process.poll() is None:
                    try:
                        child.process.wait(timeout=5.0)
                    except subprocess.TimeoutExpired:
                        child.process.kill()
                        child.process.wait(timeout=5.0)
        finally:
            for child in self._children:
                close = getattr(child.log_handle, "close", None)
                if callable(close):
                    close()
                child.ready_path.unlink(missing_ok=True)
            self._children.clear()
            self._stop_file.unlink(missing_ok=True)
            self._started = False
            self._stopping = False
            self._monitor_thread = None


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--child", action="store_true")
    parser.add_argument("--kind", choices=_KINDS)
    parser.add_argument("--data-root", type=Path)
    parser.add_argument("--marker-sha256")
    parser.add_argument("--parent-pid", type=int)
    parser.add_argument(
        "--provider-mode",
        choices=(_OPENAI_COMPATIBLE, _CODEX_VALIDATION),
    )
    parser.add_argument("--ready-file", type=Path)
    parser.add_argument("--stop-file", type=Path)
    parser.add_argument("--worker-index", type=int, default=0)
    parser.add_argument("--codex-validation", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    from code_assist_v1.knowledge_core.errors import KnowledgeCoreSchemaMismatchError

    args = _parser().parse_args(argv)
    required = (
        args.child,
        args.kind,
        args.data_root,
        args.marker_sha256,
        args.parent_pid,
        args.provider_mode,
        args.ready_file,
        args.stop_file,
    )
    if not all(required):
        raise PreviewWorkerError("The preview child worker arguments are incomplete.")
    try:
        return _run_child(args)
    except KnowledgeCoreSchemaMismatchError as error:
        try:
            print(
                json.dumps(_schema_mismatch_details(args.data_root, args.kind, error), sort_keys=True),
                file=sys.stderr,
                flush=True,
            )
        except BaseException:
            print("preview_worker_schema_diagnostic_failed", file=sys.stderr, flush=True)
        traceback.print_exc()
        return 1
    except BaseException:
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

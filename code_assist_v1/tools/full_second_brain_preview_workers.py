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
from dataclasses import dataclass
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
import traceback
from typing import Mapping


_OPENAI_COMPATIBLE = "openai-compatible"
_CODEX_VALIDATION = "codex-validation"
_KINDS = ("durable", "projector")
_DEFAULT_DURABLE_COUNT = 1
_DEFAULT_PROJECTOR_COUNT = 1


class PreviewWorkerError(RuntimeError):
    """Stable failure raised by the marker-bound preview worker supervisor."""


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


def _preview_factory(root: Path):
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
    factory = _preview_factory(root)
    layout = _storage_layout(root)
    flags = _preview_feature_flags()
    scope_resolver = ScopeResolver(flags)
    stager = FilesystemLegacyBlobStager(layout)
    publication = PublicationPreviewExtractionHandler(
        factory,
        CanonicalPublicationSourceLoader(factory, stager),
        CanonicalRichPublicationExtractor(),
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
    kernel = DurableWorkKernel(
        factory,
        HandlerRegistry(
            (
                publication.registered_handler(),
                curation.registered_handler(),
                *semantic.registered_handlers(),
            )
        ),
        scope_resolver=scope_resolver,
    )
    seeds = SemanticIntentSeedService(factory)
    seeds.assert_baseline_complete()
    triggers = SemanticSeedTriggerConsumer(factory, seeds=seeds)
    materializer = SemanticIntentMaterializer(factory, kernel, flags)

    def semantic_maintenance() -> None:
        triggers.run_batch()
        seeds.run_next_batch()
        materializer.run_batch()

    marker = _load_bound_marker(root)
    owner_suffix = str(marker["marker_sha256"])[:16]
    return DurableInferenceWorker(
        kernel,
        DurableInferenceWorkerConfig(
            claim_owner=f"full-preview-{owner_suffix}:durable:{worker_index:02d}",
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
    from code_assist_v1.tools.full_second_brain_preview import _load_marker

    return dict(_load_marker(root))


def _run_child(args: argparse.Namespace) -> int:
    root = args.data_root.resolve(strict=True)
    marker = _load_bound_marker(root)
    if marker.get("marker_sha256") != args.marker_sha256:
        raise PreviewWorkerError("The child worker marker binding changed.")
    expected_mode = _CODEX_VALIDATION if args.codex_validation else _OPENAI_COMPATIBLE
    if args.provider_mode != expected_mode:
        raise PreviewWorkerError("The child worker provider policy is inconsistent.")

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
            or not 1 <= durable_count <= 16
        ):
            raise ValueError("durable_count must be between one and sixteen.")
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

    @property
    def provider_mode(self) -> str:
        return self._policy.mode

    def start(self, *, timeout_s: float = 20.0) -> None:
        if self._children:
            raise PreviewWorkerError("The preview worker supervisor is already started.")
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
                label = f"{kind}-{worker_index:02d}"
                ready_path = worker_root / f"{self._run_id}.{label}.ready.json"
                ready_path.unlink(missing_ok=True)
                log_path = worker_root / f"{self._run_id}.{label}.log"
                log_handle = log_path.open("a", encoding="utf-8")
                command = [
                    sys.executable,
                    "-m",
                    "code_assist_v1.tools.full_second_brain_preview_workers",
                    "--child",
                    "--kind",
                    kind,
                    "--data-root",
                    os.fspath(self._root),
                    "--marker-sha256",
                    self._marker_sha256,
                    "--parent-pid",
                    str(os.getpid()),
                    "--provider-mode",
                    self._policy.mode,
                    "--worker-index",
                    str(worker_index),
                    "--ready-file",
                    os.fspath(ready_path),
                    "--stop-file",
                    os.fspath(self._stop_file),
                ]
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
                        creationflags=int(
                            getattr(subprocess, "CREATE_NO_WINDOW", 0)
                        ),
                    )
                except BaseException:
                    log_handle.close()
                    raise
                self._children.append(
                    _ManagedChild(
                        kind,
                        worker_index,
                        process,
                        log_path,
                        ready_path,
                        log_handle,
                    )
                )
            deadline = time.monotonic() + timeout_s
            for child in self._children:
                while time.monotonic() < deadline:
                    return_code = child.process.poll()
                    if return_code is not None:
                        raise PreviewWorkerError(
                            f"The {child.kind} preview worker exited with {return_code}; "
                            f"see {child.log_path}."
                        )
                    if child.ready_path.is_file():
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
        except BaseException:
            self.stop()
            raise

    def status(self) -> dict[str, object]:
        return {
            "children": [
                {
                    "alive": child.process.poll() is None,
                    "kind": child.kind,
                    "log_path": os.fspath(child.log_path),
                    "pid": child.process.pid,
                    "worker_index": child.worker_index,
                }
                for child in self._children
            ],
            "provider_mode": self._policy.mode,
            "schema_version": 1,
        }

    def stop(self, *, timeout_s: float = 20.0) -> None:
        if not self._children:
            return
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
            self._children.clear()
            self._stop_file.unlink(missing_ok=True)


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
    except BaseException:
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

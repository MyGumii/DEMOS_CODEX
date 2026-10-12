"""Production process composition and Windows stop-event boundary for P7.5.

All application/store imports are delayed until after the deployment manifest,
protected environment, filesystem, dependency, and SQLite preflight have
passed.  Importing this module starts no listener, worker, checkpoint, thread,
or scheduler.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import hashlib
import os
from pathlib import Path
import re
import sys
import threading
from typing import Callable, Mapping

from .production_config import (
    CHECKPOINT_INTERVAL_SECONDS,
    ProductionDeploymentConfig,
    SERVICE_ROWS,
    WINSW_SHA256,
)
from .production_wsgi import ProductionWSGIConfig, ProductionWSGIHost


_ENV_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,127}$", re.ASCII)
_ALLOWED_ENV_PREFIXES = (
    "AZURE_",
    "CODE_ASSIST_",
    "KNOWLEDGE_CORE_",
    "OPENAI_",
    "OUTLOOK_",
)
_AUTHORITY_ENV_NAMES = frozenset(
    {
        "CODE_ASSIST_DATA_ROOT",
        "CODE_ASSIST_BROWSER_SECURITY_PRODUCTION",
        "CODE_ASSIST_PORT",
        "CODE_ASSIST_NOTION_PORT",
        "CODE_ASSIST_PROCESS_ROLE",
        "CODE_ASSIST_STANDALONE",
        "CODE_ASSIST_TEST_DATA_ROOT",
        "CODE_ASSIST_TEST_MODE",
    }
)
_WAIT_OBJECT_0 = 0
_WAIT_TIMEOUT = 0x102
_INFINITE = 0xFFFFFFFF
_ERROR_ALREADY_EXISTS = 183
_EVENT_MODIFY_STATE = 0x0002
_SYNCHRONIZE = 0x00100000


class ProductionServiceError(RuntimeError):
    code = "PRODUCTION_SERVICE_FAILED"


class ProductionServiceAlreadyRunningError(ProductionServiceError):
    code = "PRODUCTION_SERVICE_ALREADY_RUNNING"


class ProductionServiceNotRunningError(ProductionServiceError):
    code = "PRODUCTION_SERVICE_NOT_RUNNING"


def _service_row(service_id: str) -> Mapping[str, object]:
    matches = [row for row in SERVICE_ROWS if row["service_id"] == service_id]
    if len(matches) != 1:
        raise ProductionServiceError("The requested production service is unknown.")
    return matches[0]


def parse_protected_environment(raw: bytes) -> dict[str, str]:
    """Parse the secret-bearing UTF-8/LF environment file without interpolation."""

    if not isinstance(raw, bytes):
        raise TypeError("raw must be bytes.")
    if not raw or len(raw) > 1024 * 1024 or raw.startswith(b"\xef\xbb\xbf"):
        raise ProductionServiceError("The protected environment file is invalid.")
    if b"\r" in raw or not raw.endswith(b"\n"):
        raise ProductionServiceError(
            "The protected environment file must use canonical UTF-8/LF."
        )
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise ProductionServiceError(
            "The protected environment file is not valid UTF-8."
        ) from exc
    result: dict[str, str] = {}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        if line != line.strip() or "=" not in line:
            raise ProductionServiceError(
                "A protected environment row has an invalid shape."
            )
        name, value = line.split("=", 1)
        if (
            _ENV_NAME_RE.fullmatch(name) is None
            or not name.startswith(_ALLOWED_ENV_PREFIXES)
            or name in _AUTHORITY_ENV_NAMES
            or name == "CODE_ASSIST_FAILPOINTS"
            or name.startswith("CODE_ASSIST_FAILPOINT_")
            or name in result
            or any(ord(character) < 0x20 or ord(character) == 0x7F for character in value)
        ):
            raise ProductionServiceError(
                "A protected environment name or value is not allowed."
            )
        result[name] = value
    if not result:
        raise ProductionServiceError("The protected environment file is empty.")
    return result


def _validate_protected_environment_values(values: Mapping[str, str]) -> None:
    if not isinstance(values, Mapping):
        raise TypeError("values must be a mapping of strings.")
    for name, value in values.items():
        if not isinstance(name, str) or not isinstance(value, str):
            raise TypeError("Environment names and values must be strings.")
        if (
            _ENV_NAME_RE.fullmatch(name) is None
            or not name.startswith(_ALLOWED_ENV_PREFIXES)
            or name in _AUTHORITY_ENV_NAMES
            or name == "CODE_ASSIST_FAILPOINTS"
            or name.startswith("CODE_ASSIST_FAILPOINT_")
            or any(ord(character) < 0x20 or ord(character) == 0x7F for character in value)
        ):
            raise ProductionServiceError(
                "A protected environment name or value is not allowed."
            )


def apply_production_environment(
    config: ProductionDeploymentConfig,
    *,
    service_id: str,
    values: Mapping[str, str],
    environ: dict[str, str] | None = None,
) -> dict[str, str]:
    if not isinstance(config, ProductionDeploymentConfig):
        raise TypeError("config must be ProductionDeploymentConfig.")
    row = _service_row(service_id)
    target = os.environ if environ is None else environ
    _validate_protected_environment_values(values)
    if any(
        name == "CODE_ASSIST_FAILPOINTS"
        or name.startswith("CODE_ASSIST_FAILPOINT_")
        or name in {"CODE_ASSIST_TEST_MODE", "CODE_ASSIST_TEST_DATA_ROOT"}
        for name in target
    ):
        raise ProductionServiceError(
            "Test or failpoint configuration cannot enter a production service."
        )
    local_origins = {
        f"http://127.0.0.1:{config.code_port}",
        f"http://127.0.0.1:{config.notion_port}",
    }
    configured_origins = {
        item.strip()
        for item in values.get("CODE_ASSIST_ALLOWED_ORIGINS", "").split(",")
        if item.strip()
    }
    if not local_origins.issubset(configured_origins):
        raise ProductionServiceError(
            "The protected environment does not allow both local health origins."
        )
    for name in tuple(target):
        if name.startswith(_ALLOWED_ENV_PREFIXES):
            del target[name]
    target.update(values)
    role = str(row["process_role"])
    target.update(
        {
            "CODE_ASSIST_BROWSER_SECURITY_PRODUCTION": "1",
            "CODE_ASSIST_DATA_ROOT": config.paths.data_root,
            "CODE_ASSIST_NOTION_PORT": str(config.notion_port),
            "CODE_ASSIST_PORT": str(config.code_port),
            "CODE_ASSIST_PROCESS_ROLE": role,
            "CODE_ASSIST_STANDALONE": "0",
        }
    )
    return target


def stop_event_name(deployment_id: str, service_id: str) -> str:
    if not isinstance(deployment_id, str) or not isinstance(service_id, str):
        raise TypeError("Stop-event identity values must be strings.")
    _service_row(service_id)
    digest = hashlib.sha256(f"{deployment_id}\0{service_id}".encode("ascii")).hexdigest()
    return f"Global\\CodeAssistV1Stop-{digest[:32]}"


class WindowsStopEvent:
    __slots__ = ("_closed", "_handle", "name")

    def __init__(self, handle: int, name: str) -> None:
        self._handle = handle
        self._closed = False
        self.name = name

    @classmethod
    def create(cls, name: str) -> "WindowsStopEvent":
        if sys.platform != "win32":
            raise ProductionServiceError("Production services require Windows.")
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create_event = kernel32.CreateEventW
        create_event.argtypes = (
            wintypes.LPVOID,
            wintypes.BOOL,
            wintypes.BOOL,
            wintypes.LPCWSTR,
        )
        create_event.restype = wintypes.HANDLE
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = (wintypes.HANDLE,)
        close_handle.restype = wintypes.BOOL
        ctypes.set_last_error(0)
        handle = create_event(None, True, False, name)
        error = int(ctypes.get_last_error())
        if not handle:
            raise ProductionServiceError("The service stop event could not be created.")
        if error == _ERROR_ALREADY_EXISTS:
            close_handle(handle)
            raise ProductionServiceAlreadyRunningError(
                "Another process already owns this production service identity."
            )
        return cls(int(handle), name)

    @classmethod
    def signal_existing(cls, name: str) -> None:
        if sys.platform != "win32":
            raise ProductionServiceError("Production services require Windows.")
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        open_event = kernel32.OpenEventW
        open_event.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR)
        open_event.restype = wintypes.HANDLE
        set_event = kernel32.SetEvent
        set_event.argtypes = (wintypes.HANDLE,)
        set_event.restype = wintypes.BOOL
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = (wintypes.HANDLE,)
        close_handle.restype = wintypes.BOOL
        handle = open_event(_EVENT_MODIFY_STATE | _SYNCHRONIZE, False, name)
        if not handle:
            raise ProductionServiceNotRunningError(
                "The requested production service has no active stop event."
            )
        try:
            if not set_event(handle):
                raise ProductionServiceError("The service stop event could not be set.")
        finally:
            close_handle(handle)

    @staticmethod
    def _event_functions():
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        wait = kernel32.WaitForSingleObject
        wait.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        wait.restype = wintypes.DWORD
        set_event = kernel32.SetEvent
        set_event.argtypes = (wintypes.HANDLE,)
        set_event.restype = wintypes.BOOL
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = (wintypes.HANDLE,)
        close_handle.restype = wintypes.BOOL
        return wait, set_event, close_handle

    def _require_open(self) -> int:
        if self._closed:
            raise ProductionServiceError("The service stop event is closed.")
        return self._handle

    def is_set(self) -> bool:
        wait, _set_event, _close_handle = self._event_functions()
        result = int(wait(wintypes.HANDLE(self._require_open()), 0))
        if result not in {_WAIT_OBJECT_0, _WAIT_TIMEOUT}:
            raise ProductionServiceError("The service stop event could not be observed.")
        return result == _WAIT_OBJECT_0

    def wait(self, timeout: float | None = None) -> bool:
        if timeout is None:
            milliseconds = _INFINITE
        elif isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout < 0:
            raise ValueError("timeout must be a nonnegative number or None.")
        else:
            milliseconds = min(_INFINITE - 1, int(timeout * 1000 + 0.999))
        wait, _set_event, _close_handle = self._event_functions()
        result = int(wait(wintypes.HANDLE(self._require_open()), milliseconds))
        if result not in {_WAIT_OBJECT_0, _WAIT_TIMEOUT}:
            raise ProductionServiceError("The service stop event wait failed.")
        return result == _WAIT_OBJECT_0

    def set(self) -> None:
        _wait, set_event, _close_handle = self._event_functions()
        if not set_event(wintypes.HANDLE(self._require_open())):
            raise ProductionServiceError("The service stop event could not be set.")

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        _wait, _set_event, close_handle = self._event_functions()
        if not close_handle(wintypes.HANDLE(self._handle)):
            raise ProductionServiceError("The service stop event could not be closed.")

    def __enter__(self) -> "WindowsStopEvent":
        self._require_open()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        del exc_type, exc, traceback
        self.close()


def _storage_layout(config: ProductionDeploymentConfig):
    from .migrations.storage_preflight import MigrationStorageLayout

    return MigrationStorageLayout(
        data_root=Path(config.paths.data_root),
        database=Path(config.paths.database),
        blob_root=Path(config.paths.blob_root),
        projection_root=Path(config.paths.projection_root),
    )


def _connection_factory(config: ProductionDeploymentConfig):
    from .db import ConnectionFactory
    from .migrations import CANONICAL_MIGRATION_CATALOG

    return ConnectionFactory(config.paths.database, CANONICAL_MIGRATION_CATALOG)


def run_web_service(
    config: ProductionDeploymentConfig,
    *,
    service_id: str,
    stop: object,
    host_factory: Callable[..., ProductionWSGIHost] = ProductionWSGIHost,
    application_provider: Callable[[str], object] | None = None,
) -> None:
    row = _service_row(service_id)
    application = row["application"]
    if application not in {"code", "notion"}:
        raise ProductionServiceError("The requested service is not a web host.")
    if application_provider is not None:
        if not callable(application_provider):
            raise TypeError("application_provider must be callable or None.")
        app = application_provider(str(application))
        port = config.code_port if application == "code" else config.notion_port
    elif application == "code":
        from hashlib import sha256

        from code_assist_v1.auth import ensure_secret_key
        from code_assist_v1.app_code import create_app
        from .agent_runtime import (
            canonical_cookie_principal_resolver,
            compose_canonical_agent_runtime,
        )
        from .feature_flags import read_feature_flags
        from .production_health import ProductionHealthService
        from .legacy.blob_stager import FilesystemLegacyBlobStager

        factory = _connection_factory(config)
        health = ProductionHealthService(
            factory,
            blob_root=config.paths.blob_root,
            config_sha256=config.config_sha256,
            service_id=service_id,
        )
        flags = read_feature_flags()
        if flags.personal_reads or flags.part_reads:
            cursor_secret = None
            if flags.multi_wiki_reads:
                cursor_secret = sha256(
                    b"code-assist:personal-wiki-cursor:v1\x00"
                    + ensure_secret_key().encode("utf-8")
                ).digest()
            higher_cursor_secret = None
            if flags.higher_spaces:
                higher_cursor_secret = sha256(
                    b"code-assist:higher-authority-cursor:v1\x00"
                    + ensure_secret_key().encode("utf-8")
                ).digest()
            runtime = compose_canonical_agent_runtime(
                factory,
                flags=flags,
                principal_resolver=canonical_cookie_principal_resolver(factory),
                personal_wiki_cursor_secret=cursor_secret,
                higher_authority_cursor_secret=higher_cursor_secret,
                source_artifact_stager=FilesystemLegacyBlobStager(
                    _storage_layout(config)
                ),
            )
            app = create_app(
                knowledge_core_services=runtime.services,
                knowledge_core_route_bindings=runtime.route_bindings,
                production_health_provider=health,
            )
        else:
            app = create_app(production_health_provider=health)
        port = config.code_port
    elif application == "notion":
        from code_assist_v1.app_notion import create_app
        from .production_health import ProductionHealthService

        app = create_app(
            production_health_provider=ProductionHealthService(
                _connection_factory(config),
                blob_root=config.paths.blob_root,
                config_sha256=config.config_sha256,
                service_id=service_id,
            )
        )
        port = config.notion_port
    wsgi = host_factory(
        app,
        ProductionWSGIConfig(
            service_id=str(application) + "-web",
            host=config.bind_host,
            port=port,
        ),
    )
    wsgi.serve(stop)  # type: ignore[arg-type]


def run_durable_worker_service(
    config: ProductionDeploymentConfig,
    *,
    stop: object,
) -> object:
    from .domain import FeatureFlags
    from .feature_flags import read_feature_flags
    from .inference_worker import (
        DurableInferenceWorker,
        DurableInferenceWorkerConfig,
    )
    from .legacy.blob_stager import FilesystemLegacyBlobStager
    from .scope import ScopeResolver
    from .services.publication import (
        CanonicalPublicationSourceLoader,
        PublicationPreviewExtractionHandler,
    )
    from .services.rich_publication import CanonicalRichPublicationExtractor
    from .services.curation_worker import CanonicalCurationRescanHandler
    from .services.automatic_semantic_edges import (
        AutomaticSemanticEdgeHandler,
        InternalLLMSemanticJudge,
    )
    from .services.semantic_edge_seeding import (
        SemanticIntentMaterializer,
        SemanticIntentSeedService,
        SemanticSeedTriggerConsumer,
    )
    from .worker import DurableWorkKernel, HandlerRegistry

    factory = _connection_factory(config)
    layout = _storage_layout(config)
    stager = FilesystemLegacyBlobStager(layout)
    publication = PublicationPreviewExtractionHandler(
        factory,
        CanonicalPublicationSourceLoader(factory, stager),
        CanonicalRichPublicationExtractor(),
        asset_stager=stager,
    )
    flags = read_feature_flags()
    if not isinstance(flags, FeatureFlags):
        raise ProductionServiceError("The production feature flags are invalid.")
    scope_resolver = ScopeResolver(flags)
    curation = CanonicalCurationRescanHandler(
        factory,
        scope_resolver=scope_resolver,
    )
    semantic = AutomaticSemanticEdgeHandler(
        factory,
        InternalLLMSemanticJudge(),
        model_provider="internal_llm",
        model_name="configured-provider-chain",
    )
    semantic_handlers = semantic.registered_handlers()
    kernel = DurableWorkKernel(
        factory,
        HandlerRegistry(
            (
                publication.registered_handler(),
                curation.registered_handler(),
                *semantic_handlers,
            )
        ),
        scope_resolver=scope_resolver,
    )
    seeds = SemanticIntentSeedService(factory)
    if flags.core_enabled:
        if flags.auto_semantic_edges_local:
            # First enable is deliberately fail-closed.  Operators stage the
            # baseline with both auto-edge flags off, let the bounded worker
            # drain it, and only then enable automatic evaluation.
            seeds.assert_baseline_complete()
        else:
            seeds.ensure_baseline_run()
    triggers = SemanticSeedTriggerConsumer(factory, seeds=seeds)
    materializer = SemanticIntentMaterializer(factory, kernel, flags)

    def semantic_maintenance() -> None:
        if not flags.core_enabled:
            return
        triggers.run_batch()
        seeds.run_next_batch()
        materializer.run_batch()

    worker = DurableInferenceWorker(
        kernel,
        DurableInferenceWorkerConfig(
            claim_owner=f"{config.deployment_id}:durable-worker"
        ),
        before_poll=semantic_maintenance,
    )
    return worker.serve(stop)  # type: ignore[arg-type]


def run_projector_service(
    config: ProductionDeploymentConfig,
    *,
    stop: object,
) -> tuple[object, object]:
    from .checkpoint import PassiveCheckpointCoordinator
    from .feature_flags import read_feature_flags
    from .projection.worker import OutboxProjectionWorker, ProjectionWorkerConfig

    factory = _connection_factory(config)
    flags = read_feature_flags()
    worker = OutboxProjectionWorker(
        factory,
        _storage_layout(config),
        ProjectionWorkerConfig(
            claim_owner=f"{config.deployment_id}:projector",
            footer_v2=flags.projection_footer_v2,
            aggregate_graphs=flags.aggregate_graph_reads,
            maximum_attempts=20,
            settle_space_batch_size=512,
        ),
    )
    coordinator = PassiveCheckpointCoordinator(
        factory,
        interval_seconds=CHECKPOINT_INTERVAL_SECONDS,
    )
    checkpoint_result: list[object] = []
    checkpoint_error: list[BaseException] = []

    def checkpoint_loop() -> None:
        try:
            checkpoint_result.append(coordinator.serve(stop))  # type: ignore[arg-type]
        except BaseException as exc:
            checkpoint_error.append(exc)
            setter = getattr(stop, "set", None)
            if callable(setter):
                setter()

    thread = threading.Thread(
        target=checkpoint_loop,
        name="code-assist-passive-checkpoint",
        daemon=False,
    )
    thread.start()
    worker_result: object | None = None
    worker_error: BaseException | None = None
    try:
        worker_result = worker.serve(stop)  # type: ignore[arg-type]
    except BaseException as exc:
        worker_error = exc
        setter = getattr(stop, "set", None)
        if callable(setter):
            setter()
    finally:
        thread.join(timeout=35.0)
    if thread.is_alive():
        raise ProductionServiceError(
            "The checkpoint coordinator did not drain with the projector."
        )
    if checkpoint_error:
        raise ProductionServiceError(
            "The checkpoint coordinator stopped unexpectedly."
        ) from checkpoint_error[0]
    if worker_error is not None:
        raise ProductionServiceError("The projector stopped unexpectedly.") from worker_error
    if worker_result is None or len(checkpoint_result) != 1:
        raise ProductionServiceError("The projector did not produce a service result.")
    return worker_result, checkpoint_result[0]


def run_deployment_migrator(config: ProductionDeploymentConfig) -> object:
    from .migrations import SingleMigrator

    factory = _connection_factory(config)
    return SingleMigrator(
        factory,
        app_version="p7.5-production-service",
        storage_layout=_storage_layout(config),
    ).run()


def run_production_service(
    config: ProductionDeploymentConfig,
    *,
    service_id: str,
    stop: object,
) -> object | None:
    row = _service_row(service_id)
    application = row["application"]
    if application == "deployment-migrator":
        return run_deployment_migrator(config)
    if application in {"code", "notion"}:
        run_web_service(config, service_id=service_id, stop=stop)
        return None
    if application == "durable-worker":
        return run_durable_worker_service(config, stop=stop)
    if application == "projector":
        return run_projector_service(config, stop=stop)
    raise ProductionServiceError("The production service application is unknown.")


__all__ = [
    "ProductionServiceAlreadyRunningError",
    "ProductionServiceError",
    "ProductionServiceNotRunningError",
    "WindowsStopEvent",
    "apply_production_environment",
    "parse_protected_environment",
    "run_durable_worker_service",
    "run_deployment_migrator",
    "run_production_service",
    "run_projector_service",
    "run_web_service",
    "stop_event_name",
]

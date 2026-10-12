"""Pinned production WSGI host with an explicit graceful-drain boundary.

The development entry points in :mod:`code_assist_v1.app_code` and
:mod:`code_assist_v1.app_notion` intentionally keep Flask's development
server.  Production services use this module and the exact Waitress version
frozen in ``requirements-runtime.lock``.  Importing this module neither builds
an application nor opens a listening socket.
"""

from __future__ import annotations

from dataclasses import dataclass
import importlib.metadata
import re
import threading
from typing import Any, Callable, Protocol


WAITRESS_DISTRIBUTION = "waitress"
WAITRESS_VERSION = "3.0.2"
DEFAULT_THREADS = 16
DEFAULT_CONNECTION_LIMIT = 128
DEFAULT_BACKLOG = 128
DEFAULT_CHANNEL_TIMEOUT_SECONDS = 60
DEFAULT_CLEANUP_INTERVAL_SECONDS = 30
DEFAULT_DRAIN_TIMEOUT_SECONDS = 30

_SERVICE_ID_RE = re.compile(r"^[a-z][a-z0-9-]{2,39}$", re.ASCII)


class ProductionWSGIError(RuntimeError):
    """The production HTTP host could not satisfy its frozen contract."""


class StopSignal(Protocol):
    def is_set(self) -> bool: ...

    def set(self) -> None: ...

    def wait(self, timeout: float | None = None) -> bool: ...


class _TaskDispatcher(Protocol):
    def shutdown(self, cancel_pending: bool = True, timeout: float = 5) -> bool: ...


class _WaitressServer(Protocol):
    task_dispatcher: _TaskDispatcher

    def close(self) -> object: ...

    def run(self) -> None: ...


ServerFactory = Callable[..., _WaitressServer]


@dataclass(frozen=True, slots=True)
class ProductionWSGIConfig:
    service_id: str
    host: str
    port: int
    threads: int = DEFAULT_THREADS
    connection_limit: int = DEFAULT_CONNECTION_LIMIT
    backlog: int = DEFAULT_BACKLOG
    channel_timeout_seconds: int = DEFAULT_CHANNEL_TIMEOUT_SECONDS
    cleanup_interval_seconds: int = DEFAULT_CLEANUP_INTERVAL_SECONDS
    drain_timeout_seconds: int = DEFAULT_DRAIN_TIMEOUT_SECONDS

    def __post_init__(self) -> None:
        if not isinstance(self.service_id, str) or _SERVICE_ID_RE.fullmatch(
            self.service_id
        ) is None:
            raise ValueError("service_id has an invalid production shape.")
        if self.host not in {"127.0.0.1", "0.0.0.0"}:
            raise ValueError("host must be the frozen loopback or all-interface bind.")
        for name in (
            "port",
            "threads",
            "connection_limit",
            "backlog",
            "channel_timeout_seconds",
            "cleanup_interval_seconds",
            "drain_timeout_seconds",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive integer.")
        if self.port > 65535:
            raise ValueError("port must be a valid TCP port.")
        if self.threads > 64:
            raise ValueError("threads exceeds the frozen per-process safety limit.")
        if self.connection_limit < self.threads:
            raise ValueError("connection_limit cannot be lower than threads.")
        if self.drain_timeout_seconds > 300:
            raise ValueError("drain_timeout_seconds exceeds the service stop bound.")


def assert_waitress_runtime(
    version_provider: Callable[[str], str] = importlib.metadata.version,
) -> str:
    """Require the exact reviewed server distribution before socket creation."""

    try:
        version = version_provider(WAITRESS_DISTRIBUTION)
    except importlib.metadata.PackageNotFoundError as exc:
        raise ProductionWSGIError("The production WSGI package is missing.") from exc
    except Exception as exc:
        raise ProductionWSGIError(
            "The production WSGI package version could not be verified."
        ) from exc
    if version != WAITRESS_VERSION:
        raise ProductionWSGIError(
            "The production WSGI package version differs from the runtime lock."
        )
    return version


def _waitress_factory(application: object, **kwargs: Any) -> _WaitressServer:
    from waitress import create_server

    return create_server(application, **kwargs)


class ProductionWSGIHost:
    """Run one Flask-compatible application until an explicit stop signal.

    The accept socket is closed first.  Existing worker tasks then receive the
    complete configured drain window; queued tasks are not deliberately
    cancelled.  The Windows service wrapper supplies a separate hard stop
    timeout greater than this bound.
    """

    __slots__ = ("_application", "_config", "_factory", "_version_provider")

    def __init__(
        self,
        application: object,
        config: ProductionWSGIConfig,
        *,
        server_factory: ServerFactory = _waitress_factory,
        version_provider: Callable[[str], str] = importlib.metadata.version,
    ) -> None:
        if not callable(application):
            raise TypeError("application must be a WSGI callable.")
        if not isinstance(config, ProductionWSGIConfig):
            raise TypeError("config must be ProductionWSGIConfig.")
        if not callable(server_factory) or not callable(version_provider):
            raise TypeError("Production WSGI providers must be callable.")
        self._application = application
        self._config = config
        self._factory = server_factory
        self._version_provider = version_provider

    @property
    def config(self) -> ProductionWSGIConfig:
        return self._config

    def serve(self, stop: StopSignal) -> None:
        if not all(
            callable(getattr(stop, method, None))
            for method in ("is_set", "set", "wait")
        ):
            raise TypeError("stop must expose is_set(), set(), and wait().")
        assert_waitress_runtime(self._version_provider)
        config = self._config
        try:
            server = self._factory(
                self._application,
                host=config.host,
                port=config.port,
                threads=config.threads,
                connection_limit=config.connection_limit,
                backlog=config.backlog,
                channel_timeout=config.channel_timeout_seconds,
                cleanup_interval=config.cleanup_interval_seconds,
                clear_untrusted_proxy_headers=True,
                expose_tracebacks=False,
                ident="code-assist-v1",
            )
        except Exception as exc:
            raise ProductionWSGIError(
                "The production WSGI listener could not be constructed."
            ) from exc

        close_lock = threading.Lock()
        closed = False

        def stop_accepting() -> None:
            nonlocal closed
            with close_lock:
                if closed:
                    return
                closed = True
                server.close()

        def monitor() -> None:
            stop.wait()
            stop_accepting()

        monitor_thread = threading.Thread(
            target=monitor,
            name=f"{config.service_id}-stop-monitor",
            daemon=False,
        )
        monitor_thread.start()
        run_error: BaseException | None = None
        try:
            server.run()
        except BaseException as exc:
            run_error = exc
        finally:
            stop.set()
            try:
                stop_accepting()
            finally:
                monitor_thread.join(timeout=5.0)
                server.task_dispatcher.shutdown(
                    cancel_pending=False,
                    timeout=config.drain_timeout_seconds,
                )
        if monitor_thread.is_alive():
            raise ProductionWSGIError(
                "The production WSGI stop monitor did not terminate."
            )
        if run_error is not None:
            raise ProductionWSGIError(
                "The production WSGI server stopped unexpectedly."
            ) from run_error


__all__ = [
    "DEFAULT_BACKLOG",
    "DEFAULT_CHANNEL_TIMEOUT_SECONDS",
    "DEFAULT_CLEANUP_INTERVAL_SECONDS",
    "DEFAULT_CONNECTION_LIMIT",
    "DEFAULT_DRAIN_TIMEOUT_SECONDS",
    "DEFAULT_THREADS",
    "ProductionWSGIConfig",
    "ProductionWSGIError",
    "ProductionWSGIHost",
    "WAITRESS_DISTRIBUTION",
    "WAITRESS_VERSION",
    "assert_waitress_runtime",
]

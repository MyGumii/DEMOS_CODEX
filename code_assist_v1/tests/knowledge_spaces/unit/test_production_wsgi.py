from __future__ import annotations

import threading
import time

import pytest

from code_assist_v1.knowledge_core.production_wsgi import (
    DEFAULT_BACKLOG,
    DEFAULT_CHANNEL_TIMEOUT_SECONDS,
    DEFAULT_CLEANUP_INTERVAL_SECONDS,
    DEFAULT_CONNECTION_LIMIT,
    DEFAULT_DRAIN_TIMEOUT_SECONDS,
    DEFAULT_THREADS,
    ProductionWSGIConfig,
    ProductionWSGIError,
    ProductionWSGIHost,
    WAITRESS_VERSION,
    assert_waitress_runtime,
)


class _Dispatcher:
    def __init__(self) -> None:
        self.calls: list[tuple[bool, float]] = []

    def shutdown(self, cancel_pending: bool = True, timeout: float = 5) -> bool:
        self.calls.append((cancel_pending, timeout))
        return False


class _Server:
    def __init__(self) -> None:
        self.task_dispatcher = _Dispatcher()
        self.entered = threading.Event()
        self.closed = threading.Event()
        self.close_count = 0

    def close(self) -> None:
        self.close_count += 1
        self.closed.set()

    def run(self) -> None:
        self.entered.set()
        assert self.closed.wait(5)


def _application(environ, start_response):
    del environ
    start_response("204 No Content", [])
    return []


def _config(**overrides: object) -> ProductionWSGIConfig:
    values: dict[str, object] = {
        "service_id": "code-web",
        "host": "0.0.0.0",
        "port": 10011,
    }
    values.update(overrides)
    return ProductionWSGIConfig(**values)  # type: ignore[arg-type]


def test_pinned_waitress_version_is_required() -> None:
    assert assert_waitress_runtime(lambda _name: WAITRESS_VERSION) == WAITRESS_VERSION
    with pytest.raises(ProductionWSGIError):
        assert_waitress_runtime(lambda _name: "3.0.1")


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"service_id": "Bad_Service"}, "service_id"),
        ({"host": "localhost"}, "host"),
        ({"port": 0}, "port"),
        ({"port": 65536}, "port"),
        ({"threads": 65}, "threads"),
        ({"threads": 17, "connection_limit": 16}, "connection_limit"),
        ({"drain_timeout_seconds": 301}, "drain_timeout_seconds"),
    ],
)
def test_wsgi_configuration_rejects_unfrozen_or_unbounded_values(
    overrides: dict[str, object], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        _config(**overrides)


def test_wsgi_host_binds_all_reviewed_limits_and_drains_without_cancelling() -> None:
    server = _Server()
    factory_calls: list[tuple[object, dict[str, object]]] = []

    def factory(application: object, **kwargs: object) -> _Server:
        factory_calls.append((application, kwargs))
        return server

    stop = threading.Event()
    host = ProductionWSGIHost(
        _application,
        _config(),
        server_factory=factory,
        version_provider=lambda _name: WAITRESS_VERSION,
    )
    error: list[BaseException] = []

    def run() -> None:
        try:
            host.serve(stop)
        except BaseException as exc:  # surfaced in the assertion thread
            error.append(exc)

    thread = threading.Thread(target=run, daemon=False)
    thread.start()
    assert server.entered.wait(5)
    assert server.close_count == 0
    stop.set()
    thread.join(5)

    assert not thread.is_alive()
    assert error == []
    assert server.close_count == 1
    assert server.task_dispatcher.calls == [
        (False, DEFAULT_DRAIN_TIMEOUT_SECONDS)
    ]
    assert len(factory_calls) == 1
    application, arguments = factory_calls[0]
    assert application is _application
    assert arguments == {
        "backlog": DEFAULT_BACKLOG,
        "channel_timeout": DEFAULT_CHANNEL_TIMEOUT_SECONDS,
        "cleanup_interval": DEFAULT_CLEANUP_INTERVAL_SECONDS,
        "clear_untrusted_proxy_headers": True,
        "connection_limit": DEFAULT_CONNECTION_LIMIT,
        "expose_tracebacks": False,
        "host": "0.0.0.0",
        "ident": "code-assist-v1",
        "port": 10011,
        "threads": DEFAULT_THREADS,
    }


def test_wsgi_host_closes_and_surfaces_an_unexpected_server_failure() -> None:
    class FailingServer(_Server):
        def run(self) -> None:
            self.entered.set()
            raise OSError("private listener detail")

    server = FailingServer()
    stop = threading.Event()
    host = ProductionWSGIHost(
        _application,
        _config(),
        server_factory=lambda _application, **_kwargs: server,
        version_provider=lambda _name: WAITRESS_VERSION,
    )
    with pytest.raises(ProductionWSGIError, match="stopped unexpectedly") as caught:
        host.serve(stop)
    assert "private listener detail" not in str(caught.value)
    assert stop.is_set()
    assert server.close_count == 1
    assert server.task_dispatcher.calls == [
        (False, DEFAULT_DRAIN_TIMEOUT_SECONDS)
    ]


def test_production_health_mode_is_role_bound(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CODE_ASSIST_PROCESS_ROLE", "web")
    monkeypatch.setenv("KNOWLEDGE_CORE_ENABLED", "0")
    monkeypatch.setenv("KNOWLEDGE_CORE_READS", "0")
    monkeypatch.setenv("KNOWLEDGE_CORE_PERSONAL_WRITES", "0")
    monkeypatch.setenv("KNOWLEDGE_CORE_PART_READS", "0")
    monkeypatch.setenv("KNOWLEDGE_CORE_PART_WRITES", "0")
    monkeypatch.setenv("KNOWLEDGE_CORE_PROJECTIONS", "0")
    monkeypatch.setenv("KNOWLEDGE_CORE_HIGHER_SPACES", "0")

    from code_assist_v1 import app_code, app_notion

    code_app = app_code.create_app()
    notion_app = app_notion.create_app()
    assert code_app.test_client().get("/healthz").json["mode"] == "production"
    assert notion_app.test_client().get("/healthz").json["mode"] == "production"

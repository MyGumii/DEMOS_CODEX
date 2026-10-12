"""One status GET, one owned RO handle, two separately authorized snapshots.

No pool, thread-local handoff, or authority cache. Other publication operations
retain their ordinary connection ownership. Runtime/schema/path/pragma checks
run in full at open and again before the second DEFERRED transaction.
"""
from contextlib import contextmanager
from collections.abc import Mapping
from dataclasses import dataclass
from inspect import signature
from threading import get_ident

from .services.publication import PublicationApiService, PublicationPreviewService


@dataclass(frozen=True, slots=True)
class PublicationStatusReadCoordinator:
    previews: PublicationPreviewService
    publication: PublicationApiService
    principal_resolver: object
    server_read_binding: object | None = None

    def __post_init__(self):
        if (not isinstance(self.previews, PublicationPreviewService)
                or not isinstance(self.publication, PublicationApiService)
                or self.publication._previews is not self.previews):
            raise TypeError("Status reader requires the exact publication preview service.")
        if self.server_read_binding is not None:
            from .status_read_wsgi import StatusReadServerBinding
            if type(self.server_read_binding) is not StatusReadServerBinding:
                raise TypeError("Status reader requires an explicit server binding.")
            self.server_read_binding.validate_factory(self.previews._factory)
        if (not callable(self.principal_resolver)
                or getattr(self.principal_resolver, "_canonical_read_factory", None)
                    is not self.previews._factory):
            raise TypeError("Status reader requires the same canonical cookie resolver factory.")
        # Inspect the callable actually invoked, not the @wraps target. Existing
        # zero-argument-only decorators may still use the ordinary route path.
        try:
            signature(self.principal_resolver, follow_wrapped=False).bind(
                _read_connection_provider=None)
        except (TypeError, ValueError) as exc:
            raise TypeError("Status resolver must accept the explicit read connection provider.") from exc

    def validate_bindings(self, principal_resolver, publication_service):
        if (principal_resolver is not self.principal_resolver
                or publication_service is not self.publication):
            raise TypeError("Status reader route bindings do not match.")

    def request_from_environ(self, environ):
        scope = (self.server_read_binding.from_environ(environ)
            if self.server_read_binding is not None else None)
        return self.request(_scope=scope)

    @contextmanager
    def request(self, *, _scope=None):
        if self.server_read_binding is not None:
            if _scope is None:
                raise RuntimeError("The bound status reader requires its HTTP request scope.")
            _scope.check(self.server_read_binding)
        elif _scope is not None:
            raise RuntimeError("An ordinary status reader cannot borrow a server scope.")
        reader = _PublicationStatusReadRequest(self, _scope)
        try:
            yield reader
        except BaseException as exc:
            try:
                reader.close(exc)
            except BaseException as cleanup_error:
                exc.add_note("Status read cleanup also failed: " + type(cleanup_error).__name__)
            raise
        else:
            reader.close()


class _PublicationStatusReadRequest:
    def __init__(self, coordinator, scope=None):
        self._coordinator = coordinator
        self._factory = coordinator.previews._factory
        self._connection = None
        self._thread = get_ident()
        self._phase = "new"
        self._identity = None
        self._scope = scope

    def _check_thread(self):
        if self._thread != get_ident():
            raise RuntimeError("Status reader cannot transfer between threads.")

    def _connection_for_principal(self):
        self._check_thread()
        if self._phase != "principal":
            raise RuntimeError("Status connection is only lent during principal resolution.")
        if self._connection is None:
            self._connection = (self._factory._open_revalidatable_read_only()
                if self._scope is None else self._scope.acquire(self._factory))
        return self._connection

    def resolve_principal(self):
        self._check_thread()
        if self._phase != "new":
            raise RuntimeError("Status principal can only be resolved once.")
        self._phase = "principal"
        # Invoke the supplied resolver, including wrappers; never unwrap/bypass it.
        result = self._coordinator.principal_resolver(
            _read_connection_provider=self._connection_for_principal)
        self._phase = "resolved"
        if isinstance(result, Mapping):
            self._identity = (result.get("id"), result.get("scope_epoch"))
        else:
            self._identity = (getattr(result, "id", None), getattr(result, "scope_epoch", None))
        return result

    def get_preview(self, query):
        self._check_thread()
        if self._phase != "resolved" or self._connection is None:
            raise RuntimeError("Status read requires a completed canonical principal read.")
        self._phase = "status"
        service = self._coordinator.previews
        actor_id, epoch, preview_id = service._status_query_fields(query)
        if (actor_id, epoch) != self._identity:
            raise RuntimeError("Status read identity differs from its principal phase.")
        self._factory.revalidate_read_only(self._connection)
        return service._read_preview_status(self._connection, actor_id, epoch, preview_id)

    def close(self, error=None):
        self._check_thread()
        if self._phase == "closed":
            return
        self._phase = "closed"
        if self._connection is not None:
            if self._scope is None:
                self._connection.close()
            else:
                self._scope.release(error)

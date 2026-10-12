"""One maintenance invocation, two fresh RO phases; no cached query results."""
from contextlib import contextmanager
import threading

from .db import ConnectionFactory
from .errors import KnowledgeCoreTransactionError


_CAPABILITY = object()


class _MaintenanceReadScope:
    def __init__(self, capability, factory, lifecycle):
        if capability is not _CAPABILITY:
            raise TypeError("Maintenance read scopes require internal ownership.")
        self.__factory, self.__lifecycle = factory, lifecycle
        self.__thread = threading.current_thread()
        self.__active, self.__busy, self.__failure = True, False, None

    def __getstate__(self):
        raise TypeError("A maintenance read scope cannot leave its invocation.")

    @contextmanager
    def borrow(self, factory):
        if (factory is not self.__factory or threading.current_thread() is not self.__thread
                or not self.__active or self.__busy):
            raise KnowledgeCoreTransactionError("Maintenance read scope ownership or state differs.")
        if self.__failure is not None:
            raise self.__failure
        self.__busy = True
        try:
            with self.__lifecycle.borrow() as connection:
                factory._check_owned_read_only(connection)
                yield connection
        except BaseException as error:
            self.__failure = error
            raise
        finally:
            self.__busy = False

    def expire(self):
        # Coordinator owns expiration on the same synchronous call stack.
        self.__active = False

    def require_success(self):
        if self.__failure is not None:
            raise self.__failure


@contextmanager
def maintenance_read_connection(factory, scope):
    if scope is None:
        connection = factory.open(read_only=True)
        try:
            yield connection
        finally:
            connection.close()
        return
    if type(scope) is not _MaintenanceReadScope:
        raise TypeError("Maintenance reads require an explicit invocation scope.")
    with scope.borrow(factory) as connection:
        yield connection


def run_semantic_maintenance(triggers, seeds, materializer, *, reuse_reads=False):
    """Preserve trigger → seed → materialize order and all mutation boundaries.

    Default retains three independent ordinary operations. The opt-in owns one
    physical RO lifecycle for the first two read phases only. Each service ends
    its read transaction/lease before performing its existing independent writes.
    The lifecycle closes before materialization. Nothing survives this call.
    """
    if type(reuse_reads) is not bool:
        raise TypeError("Maintenance read reuse must be an explicit boolean.")
    if not reuse_reads:
        triggers.run_batch()
        seeds.run_next_batch()
        materializer.run_batch()
        return
    from .services.semantic_edge_seeding import (
        SemanticIntentMaterializer, SemanticIntentSeedService, SemanticSeedTriggerConsumer,
    )
    if (type(triggers) is not SemanticSeedTriggerConsumer
            or type(seeds) is not SemanticIntentSeedService
            or type(materializer) is not SemanticIntentMaterializer):
        raise TypeError("Maintenance reuse requires the canonical services.")
    factory = seeds._factory
    if (type(factory) is not ConnectionFactory or triggers._factory is not factory
            or materializer._factory is not factory or triggers._seeds is not seeds):
        raise KnowledgeCoreTransactionError("Maintenance service bindings differ.")
    with factory._read_only_lifecycle() as lifecycle:
        scope = _MaintenanceReadScope(_CAPABILITY, factory, lifecycle)
        try:
            triggers.run_batch(_read_scope=scope)
            seeds.run_next_batch(_read_scope=scope)
            scope.require_success()
        except BaseException as primary:
            scope.expire()
            try:
                lifecycle.close()
            except BaseException as cleanup:
                primary.add_note("Maintenance read cleanup failed: " + type(cleanup).__name__)
            raise
        else:
            scope.expire()
            # Close inside the factory context so its exception cleanup can
            # retry a failed physical close without repeating either query.
            lifecycle.close()
    materializer.run_batch()

"""Explicit invocation-owned preflight reads; no cached authority or thread pool."""
from contextlib import contextmanager
import threading

from .db import ConnectionFactory
from .errors import KnowledgeCoreError
from .repositories.jobs import ClaimFence


class PreparationReadError(KnowledgeCoreError):
    code = "JOB_PREPARATION_READ_SCOPE_INVALID"


_CAPABILITY = object()


def _finish(scope, lifecycle, *, propagate_failure):
    """Close while still inside the factory context's cleanup safety net."""
    try:
        scope.expire(propagate_failure=propagate_failure)
    except BaseException as primary:
        try:
            lifecycle.close()
        except BaseException as cleanup:
            primary.add_note("Preparation lifecycle close failed: " + type(cleanup).__name__)
        raise
    else:
        lifecycle.close()


class PreparationReadBinding:
    """One exact factory; scopes retain neither results nor shared live handles."""

    def __init__(self, factory):
        if type(factory) is not ConnectionFactory:
            raise TypeError("Preparation reads require an exact canonical factory.")
        self.__factory = factory
        self.__lock = threading.Lock()
        self.__counts = dict(invocations_started=0, invocations_finished=0,
            invocation_errors=0, callbacks_started=0, callbacks_succeeded=0,
            callback_errors=0, owner_thread_borrows=0, foreign_thread_callbacks=0)

    def validate_factory(self, factory):
        if factory is not self.__factory:
            raise PreparationReadError("Preparation scope factory changed.")

    def _record(self, key):
        with self.__lock:
            self.__counts[key] += 1

    def snapshot(self):
        with self.__lock:
            return dict(self.__counts)

    @contextmanager
    def invocation(self, kernel, factory, fence):
        self.validate_factory(factory)
        if type(fence) is not ClaimFence:
            raise TypeError("Preparation reads require a claim fence.")
        self._record("invocations_started")
        try:
            with factory._read_only_lifecycle() as lifecycle:
                scope = _PreparationReadScope(_CAPABILITY, self, kernel, factory, fence, lifecycle)
                try:
                    yield scope
                except BaseException as primary:
                    try:
                        _finish(scope, lifecycle, propagate_failure=False)
                    except BaseException as cleanup:
                        primary.add_note("Preparation scope expiration failed: " + type(cleanup).__name__)
                    raise
                else:
                    _finish(scope, lifecycle, propagate_failure=True)
        except BaseException:
            self._record("invocation_errors")
            raise
        finally:
            # Finished is termination accounting, NOT a claim that a failed
            # physical close succeeded. Errors remain separately visible.
            self._record("invocations_finished")

    def run(self, scope, kernel, factory, fence, deadline):
        if type(scope) is not _PreparationReadScope:
            raise PreparationReadError("An explicit preparation read scope is required.")
        return scope.run(self, kernel, factory, fence, deadline)


class _PreparationReadScope:
    def __init__(self, capability, binding, kernel, factory, fence, lifecycle):
        if capability is not _CAPABILITY:
            raise TypeError("Invalid preparation read capability.")
        self.__binding, self.__kernel, self.__factory = binding, kernel, factory
        self.__fence, self.__lifecycle = fence, lifecycle
        self.__owner = threading.current_thread()
        self.__lock = threading.RLock()
        self.__active, self.__busy, self.__failure = True, False, None

    def __getstate__(self):
        raise TypeError("A preparation scope cannot leave its invocation.")

    def expire(self, *, propagate_failure):
        if threading.current_thread() is not self.__owner:
            raise PreparationReadError("Only the invocation owner may expire its scope.")
        # Wait for a foreign callback's short transaction/close. A queued later
        # callback sees inactive; no transaction or raw handle crosses threads.
        with self.__lock:
            if self.__busy:
                raise PreparationReadError("A preparation scope cannot expire reentrantly.")
            self.__active = False
            if propagate_failure and self.__failure is not None:
                raise self.__failure

    @contextmanager
    def __foreign_connection(self):
        self.__binding._record("foreign_thread_callbacks")
        # One physical connection per foreign callback, entirely owned/closed
        # on that thread. A fresh wrapper is revoked before physical cleanup,
        # including close failure. No foreign handle is parked between calls.
        with self.__factory._read_only_lifecycle() as local:
            with local.borrow() as connection:
                yield connection
            # Surface a close error, then let the enclosing factory context
            # retry cleanup on this same Thread without repeating the query.
            local.close()

    def run(self, binding, kernel, factory, fence, deadline):
        with self.__lock:
            if (binding is not self.__binding or kernel is not self.__kernel
                    or factory is not self.__factory or type(fence) is not ClaimFence
                    or fence != self.__fence):
                raise PreparationReadError("Preparation scope binding or claim changed.")
            if not self.__active:
                raise PreparationReadError("Preparation scope expired.")
            if self.__failure is not None:
                raise self.__failure
            if self.__busy:
                error = PreparationReadError("Preparation callbacks cannot nest.")
                self.__failure = error
                raise error
            self.__busy = True
            binding._record("callbacks_started")
            try:
                if threading.current_thread() is self.__owner:
                    connection_scope = self.__lifecycle.borrow()
                    binding._record("owner_thread_borrows")
                else:
                    # Explicit compatibility path for the Codex stdin Thread:
                    # full ordinary open/check/transaction/close on THAT thread.
                    connection_scope = self.__foreign_connection()
                with connection_scope as connection:
                    result = kernel._preflight_preparation_phase(
                        connection, fence, deadline_at_db_ms=deadline)
                if self.__failure is not None:  # A handler swallowed a nested failure.
                    raise self.__failure
                binding._record("callbacks_succeeded")
                return result
            except BaseException as error:
                if self.__failure is None:
                    self.__failure = error
                binding._record("callback_errors")
                raise self.__failure
            finally:
                self.__busy = False

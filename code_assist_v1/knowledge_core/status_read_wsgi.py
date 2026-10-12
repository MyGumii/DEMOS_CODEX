"""Explicit pinned-WSGI thread ownership for opt-in publication status reads.

No global/thread-local lookup, principal cache, or raw connection is exposed.
The dispatcher passes its lexical owner to one parsed HTTP request; a private
WSGI environment value passes that request-scoped capability to the coordinator.
"""
from contextlib import contextmanager
import threading

from .db import ConnectionFactory
from .production_wsgi import ProductionWSGIError, assert_waitress_runtime


STATUS_READ_ENVIRON_KEY = "code_assist.publication_status_read_scope"
_REQUEST_ATTRIBUTE = "_code_assist_status_read_scope"
_CAPABILITY = object()


class StatusReadServerBinding:
    """One exact factory bound explicitly to one app/server composition."""

    def __init__(self, factory):
        if type(factory) is not ConnectionFactory:
            raise TypeError("A canonical connection factory is required.")
        self.__factory = factory
        self.__lock = threading.Lock()
        self.__counts = dict(threads_started=0, threads_closed=0, thread_close_errors=0,
            requests_started=0, requests_closed=0, loans_started=0,
            loans_returned=0, loan_errors=0, leaked_loans=0)

    def validate_factory(self, factory):
        if factory is not self.__factory:
            raise TypeError("Status read server and application factories differ.")

    def _record(self, name):
        with self.__lock:
            self.__counts[name] += 1

    def snapshot(self):
        with self.__lock:
            return dict(self.__counts)

    @contextmanager
    def thread_owner(self):
        self._record("threads_started")
        try:
            with self.__factory._read_only_lifecycle() as lifecycle:
                yield _ThreadOwner(_CAPABILITY, self, lifecycle)
        except BaseException:
            self._record("thread_close_errors")
            raise
        else:
            self._record("threads_closed")

    def request_scope(self, owner):
        owner.check(self)
        return _RequestScope(_CAPABILITY, self, owner)

    def from_environ(self, environ):
        scope = environ.get(STATUS_READ_ENVIRON_KEY)
        if type(scope) is not _RequestScope:
            raise ProductionWSGIError("The explicit status read request scope is missing.")
        scope.check(self)
        return scope


class _ThreadOwner:
    def __init__(self, capability, binding, lifecycle):
        if capability is not _CAPABILITY:
            raise TypeError("Invalid status read owner capability.")
        self.binding, self.lifecycle = binding, lifecycle
        self.thread = threading.current_thread()

    def check(self, binding):
        if self.binding is not binding or threading.current_thread() is not self.thread:
            raise ProductionWSGIError("Status read owner cannot transfer between factories or threads.")


class _RequestScope:
    def __init__(self, capability, binding, owner):
        if capability is not _CAPABILITY:
            raise TypeError("Invalid status read request capability.")
        self.__binding, self.__owner = binding, owner
        self.__active, self.__used, self.__lease = True, False, None
        binding._record("requests_started")

    def check(self, binding):
        self.__owner.check(binding)
        if binding is not self.__binding or not self.__active:
            raise ProductionWSGIError("The status read request scope has expired.")

    def acquire(self, factory):
        self.check(self.__binding)
        self.__binding.validate_factory(factory)
        if self.__used:
            raise ProductionWSGIError("Only one status read loan is allowed per request.")
        self.__used = True
        lease = self.__owner.lifecycle.borrow()
        connection = lease.__enter__()
        self.__lease = lease
        self.__binding._record("loans_started")
        return connection

    def release(self, error=None):
        self.__owner.check(self.__binding)
        lease, self.__lease = self.__lease, None
        if lease is None:
            return
        try:
            lease.__exit__(type(error) if error is not None else None, error,
                error.__traceback__ if error is not None else None)
        except BaseException:
            self.__binding._record("loan_errors")
            raise
        else:
            self.__binding._record("loan_errors" if error is not None else "loans_returned")

    def close(self):
        self.__owner.check(self.__binding)
        if not self.__active:
            return
        self.__active = False
        try:
            if self.__lease is not None:
                self.__binding._record("leaked_loans")
                error = ProductionWSGIError("A status read loan escaped its request.")
                try:
                    self.release(error)
                except BaseException as cleanup_error:
                    error.add_note("Leaked read cleanup failed: " + type(cleanup_error).__name__)
                raise error
        finally:
            self.__binding._record("requests_closed")


def create_status_read_server(application, binding, **kwargs):
    """Create the pinned adapter without changing the default Waitress path."""
    if type(binding) is not StatusReadServerBinding:
        raise TypeError("An explicit status read server binding is required.")
    assert_waitress_runtime()
    from waitress import create_server
    from waitress.task import ThreadedTaskDispatcher, WSGITask

    class OwnedDispatcher(ThreadedTaskDispatcher):
        # The pinned dispatch loop is intentionally local: thread-exit accounting
        # must occur AFTER same-thread resource close, not before a finally hook.
        def __init__(self):
            super().__init__()
            self.resource_errors = []
            self.closing_threads = set()

        def set_thread_count(self, count):
            # Keep closing threads visible to shutdown's join predicate, but
            # never count them as workers that need another stop request.
            with self.lock:
                running = len(self.threads) - self.stop_count - len(self.closing_threads)
                thread_no = 0
                while running < count:
                    while thread_no in self.threads:
                        thread_no += 1
                    self.threads.add(thread_no)
                    self.start_new_thread(self.handler_thread, thread_no)
                    self.active_count += 1
                    running += 1
                    thread_no += 1
                if running > count:
                    self.stop_count += running - count
                    self.queue_cv.notify_all()

        def handler_thread(self, thread_no):
            active = True
            consumed_stop = False
            try:
                with binding.thread_owner() as owner:
                    while True:
                        with self.lock:
                            while not self.queue and self.stop_count == 0:
                                self.active_count -= 1
                                active = False
                                try:
                                    self.queue_cv.wait()
                                finally:
                                    self.active_count += 1
                                    active = True
                            if self.stop_count > 0:
                                self.stop_count -= 1
                                consumed_stop = True
                                self.closing_threads.add(thread_no)
                                break
                            task = self.queue.popleft()
                        try:
                            task.service_with_status_read_owner(owner)
                        except BaseException:
                            self.logger.exception("Exception servicing an owned HTTP task")
            except BaseException as error:
                with self.lock:
                    self.resource_errors.append(type(error).__name__)
            finally:
                with self.lock:
                    if active:
                        self.active_count -= 1
                    if not consumed_stop and self.stop_count > 0:
                        self.stop_count -= 1
                    self.closing_threads.discard(thread_no)
                    self.threads.discard(thread_no)
                    self.thread_exit_cv.notify_all()

        def shutdown(self, cancel_pending=True, timeout=5):
            result = super().shutdown(cancel_pending=cancel_pending, timeout=timeout)
            with self.lock:
                failed = bool(self.resource_errors)
            if failed:
                raise ProductionWSGIError("Status read thread resource cleanup failed.")
            return result

    dispatcher = OwnedDispatcher()
    server = None
    try:
        server = create_server(application, _dispatcher=dispatcher, **kwargs)
        original_channel = server.channel_class

        class OwnedTask(WSGITask):
            def get_environment(self):
                environ = super().get_environment()
                scope = getattr(self.request, _REQUEST_ATTRIBUTE, None)
                if type(scope) is not _RequestScope:
                    raise ProductionWSGIError("HTTP task has no owned status scope.")
                scope.check(binding)
                environ[STATUS_READ_ENVIRON_KEY] = scope
                return environ

        class OwnedChannel(original_channel):
            task_class = OwnedTask

            def service_with_status_read_owner(self, owner):
                # Bind the parsed request, NOT the keep-alive channel: the next
                # request can be queued to another thread before service returns.
                with self.requests_lock:
                    parsed = self.requests[0]
                if getattr(parsed, _REQUEST_ATTRIBUTE, None) is not None:
                    raise ProductionWSGIError("HTTP request scope is already bound.")
                scope = binding.request_scope(owner)
                setattr(parsed, _REQUEST_ATTRIBUTE, scope)
                try:
                    self.service()
                finally:
                    try:
                        scope.close()
                    finally:
                        delattr(parsed, _REQUEST_ATTRIBUTE)

        server.channel_class = OwnedChannel
        dispatcher.set_thread_count(kwargs["threads"])
        return server
    except BaseException as error:
        try:
            if server is not None:
                server.close()
            dispatcher.shutdown()
        except BaseException as cleanup_error:
            error.add_note("Status read server cleanup failed: " + type(cleanup_error).__name__)
        raise

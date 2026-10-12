"""Invocation-local authority checks shared by provider submission boundaries."""
from __future__ import annotations

from threading import RLock
from typing import Callable


class InvocationGuard:
    """Latch the first failure across nested adapters and a CLI writer thread.

    This is not a grant, token, lock on revocation, or serializable model input.
    The callback owns short fresh authority checks and must not hold a database
    transaction across provider work. Once failed, this instance never recovers.
    """

    def __init__(self, callback: Callable[[], None]) -> None:
        if not callable(callback):
            raise TypeError("An invocation guard callback is required.")
        self._callback = callback
        self._failure: BaseException | None = None
        self._lock = RLock()

    def raise_if_failed(self) -> None:
        with self._lock:
            if self._failure is not None:
                raise self._failure

    def __call__(self) -> None:
        with self._lock:
            self.raise_if_failed()
            try:
                self._callback()
            except BaseException as exc:
                self._failure = exc
                raise

    def __getstate__(self):
        raise TypeError("An invocation guard cannot leave its invocation.")

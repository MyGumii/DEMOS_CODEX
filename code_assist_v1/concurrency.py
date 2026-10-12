"""
Small concurrency primitives shared by Flask routes and stores.
"""
from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from typing import Iterator

from code_assist_v1.auth import safe_user_id


_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.RLock()


def _lock_key(scope: str, *parts: object) -> str:
    cleaned = [str(scope or "global")]
    for part in parts:
        text = str(part or "default").strip().lower()
        cleaned.append(text[:120] or "default")
    return ":".join(cleaned)


def get_lock(scope: str, *parts: object) -> threading.RLock:
    key = _lock_key(scope, *parts)
    with _LOCKS_GUARD:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _LOCKS[key] = lock
        return lock


@contextmanager
def scoped_lock(scope: str, *parts: object) -> Iterator[None]:
    lock = get_lock(scope, *parts)
    lock.acquire()
    try:
        yield
    finally:
        lock.release()


@contextmanager
def user_write_lock(scope: str, user_id: str | None, *parts: object) -> Iterator[None]:
    uid = safe_user_id(user_id) or "__anonymous__"
    with scoped_lock(scope, uid, *parts):
        yield


GGUF_MAX_CONCURRENT = max(1, int(os.environ.get("CODE_ASSIST_GGUF_MAX_CONCURRENT", "1") or "1"))
GGUF_WAIT_TIMEOUT_SEC = max(1, int(os.environ.get("CODE_ASSIST_GGUF_WAIT_TIMEOUT_SEC", "900") or "900"))
_GGUF_SEMAPHORE = threading.BoundedSemaphore(GGUF_MAX_CONCURRENT)


@contextmanager
def gguf_inference_slot(model_key: str | None = None) -> Iterator[None]:
    """Limit concurrent access to the process-global llama.cpp runtime.

    The current GGUF loader keeps one global model instance, so the safe default
    is one concurrent inference. Operators can raise CODE_ASSIST_GGUF_MAX_CONCURRENT
    after validating their llama.cpp build and model loading strategy.
    """
    from code_assist_v1.model_runtime import assert_local_inference_allowed

    assert_local_inference_allowed()
    acquired = _GGUF_SEMAPHORE.acquire(timeout=GGUF_WAIT_TIMEOUT_SEC)
    if not acquired:
        key = f" for {model_key}" if model_key else ""
        raise TimeoutError(f"GGUF inference queue timed out{key}")
    try:
        yield
    finally:
        _GGUF_SEMAPHORE.release()

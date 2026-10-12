"""Process-role and single-owner boundary for local GGUF inference.

Development mode preserves the historical in-process GGUF behavior.  A
production web process may advertise or call remote API models, but it may not
discover, load, or execute a local GGUF model.  A production worker may use a
local GGUF model only while it owns the machine-wide Windows named mutex and
is bound to an absolute canonical database path.

Nothing in this module starts a worker, opens a model, or acquires ownership at
import time.
"""

from __future__ import annotations

import ctypes
import os
import sys
import threading
from contextlib import contextmanager
from enum import Enum
from pathlib import Path
from typing import Iterator, Mapping


PROCESS_ROLE_ENV = "CODE_ASSIST_PROCESS_ROLE"
_MUTEX_NAME = "Global\\CodeAssistV1InferenceOwnerV1"
_WAIT_OBJECT_0 = 0x00000000
_WAIT_ABANDONED = 0x00000080
_WAIT_TIMEOUT = 0x00000102


class ModelRuntimeError(RuntimeError):
    """Base error for the process/model ownership boundary."""


class ModelRuntimeConfigurationError(ModelRuntimeError):
    """The deployment selected an invalid or incompatible process role."""


class LocalInferenceOwnershipError(ModelRuntimeError):
    """The current process is not the sole authorized local inference owner."""


class InferenceOwnerBusyError(LocalInferenceOwnershipError):
    """Another process already owns local inference on this server."""


class ProcessRole(str, Enum):
    DEVELOPMENT = "development"
    MIGRATOR = "migrator"
    WEB = "web"
    WORKER = "worker"
    PROJECTOR = "projector"


def process_role(environ: Mapping[str, str] | None = None) -> ProcessRole:
    """Return the exact process role; the compatibility default is development."""

    environment = os.environ if environ is None else environ
    raw = environment.get(PROCESS_ROLE_ENV, ProcessRole.DEVELOPMENT.value)
    try:
        return ProcessRole(raw)
    except (TypeError, ValueError) as exc:
        raise ModelRuntimeConfigurationError(
            f"{PROCESS_ROLE_ENV} must be exactly 'development', 'migrator', "
            "'web', 'worker', or 'projector'."
        ) from exc


def assert_web_process_role(environ: Mapping[str, str] | None = None) -> ProcessRole:
    """Reject accidentally composing a Flask host in the worker process."""

    role = process_role(environ)
    if role in {
        ProcessRole.MIGRATOR,
        ProcessRole.WORKER,
        ProcessRole.PROJECTOR,
    }:
        raise ModelRuntimeConfigurationError(
            "A production background process cannot host web routes."
        )
    return role


def assert_worker_process_role(
    environ: Mapping[str, str] | None = None,
) -> ProcessRole:
    """Require the explicit production worker role."""

    role = process_role(environ)
    if role is not ProcessRole.WORKER:
        raise ModelRuntimeConfigurationError(
            "The durable inference service requires CODE_ASSIST_PROCESS_ROLE=worker."
        )
    return role


def gguf_catalog_enabled(environ: Mapping[str, str] | None = None) -> bool:
    """Only development web hosts retain local-model discovery/listing."""

    return process_role(environ) is ProcessRole.DEVELOPMENT


def assert_legacy_background_jobs_allowed(
    environ: Mapping[str, str] | None = None,
) -> None:
    """Keep the historical in-process executor/scheduler development-only."""

    if process_role(environ) is not ProcessRole.DEVELOPMENT:
        raise ModelRuntimeConfigurationError(
            "In-process background jobs are disabled outside development; "
            "enqueue a canonical durable job instead."
        )


_ACTIVE_MUTEXES: set[str] = set()
_ACTIVE_MUTEXES_GUARD = threading.RLock()


def _normalized_database_identity(database_path: str | os.PathLike[str]) -> str:
    path = Path(database_path)
    if not path.is_absolute():
        raise ModelRuntimeConfigurationError(
            "The inference owner requires an absolute canonical database path."
        )
    return os.path.normcase(os.fspath(path.resolve(strict=False)))


def inference_mutex_name(database_path: str | os.PathLike[str]) -> str:
    """Return the machine-wide GPU ownership name.

    The database path is still validated because every production worker is
    bound to one canonical database, but it deliberately does not scope the
    mutex.  A second deployment or stale configuration on the same Windows
    GPU server must not become another local-model owner merely by pointing at
    a different database.
    """

    _normalized_database_identity(database_path)
    return _MUTEX_NAME


def _mark_active(name: str) -> None:
    with _ACTIVE_MUTEXES_GUARD:
        if _ACTIVE_MUTEXES:
            raise InferenceOwnerBusyError(
                "Local inference ownership is already active in this process."
            )
        _ACTIVE_MUTEXES.add(name)


def _mark_inactive(name: str) -> None:
    with _ACTIVE_MUTEXES_GUARD:
        _ACTIVE_MUTEXES.discard(name)


def _has_active_owner() -> bool:
    with _ACTIVE_MUTEXES_GUARD:
        return bool(_ACTIVE_MUTEXES)


def assert_local_inference_allowed(
    environ: Mapping[str, str] | None = None,
) -> None:
    """Fail closed unless development compatibility or the sole worker owns it."""

    role = process_role(environ)
    if role is ProcessRole.DEVELOPMENT:
        return
    if role is ProcessRole.WEB:
        raise LocalInferenceOwnershipError(
            "Local GGUF inference is disabled in production web processes."
        )
    if not _has_active_owner():
        raise LocalInferenceOwnershipError(
            "The worker does not hold the central local-inference ownership lease."
        )


@contextmanager
def hold_inference_ownership(
    database_path: str | os.PathLike[str],
    *,
    environ: Mapping[str, str] | None = None,
) -> Iterator[str]:
    """Acquire the machine-wide Windows GPU mutex without waiting.

    The mutex is held for the complete worker-service lifetime.  A crashed
    process releases its kernel handle automatically; no mutable file lease or
    process-memory queue is authoritative.
    """

    assert_worker_process_role(environ)
    if sys.platform != "win32":
        raise ModelRuntimeConfigurationError(
            "The production inference owner is implemented for the Windows target."
        )
    name = inference_mutex_name(database_path)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_mutex = kernel32.CreateMutexW
    create_mutex.argtypes = (ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p)
    create_mutex.restype = ctypes.c_void_p
    wait_for_single_object = kernel32.WaitForSingleObject
    wait_for_single_object.argtypes = (ctypes.c_void_p, ctypes.c_uint32)
    wait_for_single_object.restype = ctypes.c_uint32
    release_mutex = kernel32.ReleaseMutex
    release_mutex.argtypes = (ctypes.c_void_p,)
    release_mutex.restype = ctypes.c_int
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (ctypes.c_void_p,)
    close_handle.restype = ctypes.c_int

    handle = create_mutex(None, 0, name)
    if not handle:
        raise LocalInferenceOwnershipError(
            "The central local-inference ownership mutex could not be created."
        )
    wait_result = int(wait_for_single_object(handle, 0))
    if wait_result == _WAIT_TIMEOUT:
        close_handle(handle)
        raise InferenceOwnerBusyError(
            "Another process already owns local inference on this server."
        )
    if wait_result not in {_WAIT_OBJECT_0, _WAIT_ABANDONED}:
        close_handle(handle)
        raise LocalInferenceOwnershipError(
            "The central local-inference ownership mutex could not be acquired."
        )
    try:
        _mark_active(name)
        yield name
    finally:
        _mark_inactive(name)
        release_mutex(handle)
        close_handle(handle)


__all__ = [
    "InferenceOwnerBusyError",
    "LocalInferenceOwnershipError",
    "ModelRuntimeConfigurationError",
    "ModelRuntimeError",
    "PROCESS_ROLE_ENV",
    "ProcessRole",
    "assert_local_inference_allowed",
    "assert_legacy_background_jobs_allowed",
    "assert_web_process_role",
    "assert_worker_process_role",
    "gguf_catalog_enabled",
    "hold_inference_ownership",
    "inference_mutex_name",
    "process_role",
]

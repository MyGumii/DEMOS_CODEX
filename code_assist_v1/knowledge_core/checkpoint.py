"""Single-owner periodic PASSIVE WAL checkpoint coordinator for P7.5."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import ctypes
import os
import sys
from typing import Callable, Iterator, Mapping, Protocol

from .db import ConnectionFactory, passive_wal_checkpoint


CHECKPOINT_MUTEX_NAME = "Global\\CodeAssistV1CheckpointCoordinatorV1"
_WAIT_OBJECT_0 = 0
_WAIT_ABANDONED = 0x80
_WAIT_TIMEOUT = 0x102


class CheckpointCoordinatorError(RuntimeError):
    code = "CHECKPOINT_COORDINATOR_ERROR"


class CheckpointOwnerBusyError(CheckpointCoordinatorError):
    code = "CHECKPOINT_COORDINATOR_BUSY"


class StopSignal(Protocol):
    def is_set(self) -> bool: ...

    def wait(self, timeout: float | None = None) -> bool: ...


@dataclass(frozen=True, slots=True)
class PassiveCheckpointResult:
    busy: int
    log_frames: int
    checkpointed_frames: int

    def __post_init__(self) -> None:
        for name in ("busy", "log_frames", "checkpointed_frames"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError("PASSIVE checkpoint counters must be nonnegative integers.")


@dataclass(frozen=True, slots=True)
class CheckpointServiceResult:
    attempts: int
    busy_count: int
    log_frames: int
    checkpointed_frames: int


@contextmanager
def hold_checkpoint_ownership(
    database_path: str | os.PathLike[str],
) -> Iterator[str]:
    path = os.fspath(database_path)
    if not path or not os.path.isabs(path):
        raise CheckpointCoordinatorError(
            "Checkpoint ownership requires an absolute canonical database path."
        )
    if sys.platform != "win32":
        raise CheckpointCoordinatorError(
            "Production checkpoint ownership is implemented for Windows."
        )
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
    handle = create_mutex(None, 0, CHECKPOINT_MUTEX_NAME)
    if not handle:
        raise CheckpointCoordinatorError(
            "The checkpoint ownership mutex could not be created."
        )
    wait_result = int(wait_for_single_object(handle, 0))
    if wait_result == _WAIT_TIMEOUT:
        close_handle(handle)
        raise CheckpointOwnerBusyError(
            "Another process already owns the checkpoint coordinator."
        )
    if wait_result not in {_WAIT_OBJECT_0, _WAIT_ABANDONED}:
        close_handle(handle)
        raise CheckpointCoordinatorError(
            "The checkpoint ownership mutex could not be acquired."
        )
    try:
        yield CHECKPOINT_MUTEX_NAME
    finally:
        release_mutex(handle)
        close_handle(handle)


CheckpointOperation = Callable[[ConnectionFactory], tuple[int, int, int]]
OwnershipProvider = Callable[[str | os.PathLike[str]], object]


class PassiveCheckpointCoordinator:
    __slots__ = ("_checkpoint", "_factory", "_interval", "_ownership")

    def __init__(
        self,
        factory: ConnectionFactory,
        *,
        interval_seconds: int = 300,
        checkpoint: CheckpointOperation = passive_wal_checkpoint,
        ownership: OwnershipProvider = hold_checkpoint_ownership,
    ) -> None:
        if not isinstance(factory, ConnectionFactory):
            raise TypeError("factory must be a ConnectionFactory.")
        if (
            isinstance(interval_seconds, bool)
            or not isinstance(interval_seconds, int)
            or interval_seconds < 1
            or interval_seconds > 3600
        ):
            raise ValueError("interval_seconds must be in the range 1..3600.")
        if not callable(checkpoint) or not callable(ownership):
            raise TypeError("Checkpoint providers must be callable.")
        self._factory = factory
        self._interval = interval_seconds
        self._checkpoint = checkpoint
        self._ownership = ownership

    def run_once(self) -> PassiveCheckpointResult:
        busy, log_frames, checkpointed_frames = self._checkpoint(self._factory)
        return PassiveCheckpointResult(busy, log_frames, checkpointed_frames)

    def serve(self, stop: StopSignal) -> CheckpointServiceResult:
        if not callable(getattr(stop, "is_set", None)) or not callable(
            getattr(stop, "wait", None)
        ):
            raise TypeError("stop must expose is_set() and wait().")
        attempts = busy_count = log_frames = checkpointed_frames = 0
        ownership = self._ownership(self._factory.database_path)
        enter = getattr(ownership, "__enter__", None)
        exit_method = getattr(ownership, "__exit__", None)
        if not callable(enter) or not callable(exit_method):
            raise CheckpointCoordinatorError(
                "Checkpoint ownership provider returned an invalid context."
            )
        with ownership:  # type: ignore[attr-defined]
            while not stop.is_set():
                result = self.run_once()
                attempts += 1
                busy_count += result.busy
                log_frames += result.log_frames
                checkpointed_frames += result.checkpointed_frames
                if stop.wait(self._interval):
                    break
        return CheckpointServiceResult(
            attempts=attempts,
            busy_count=busy_count,
            log_frames=log_frames,
            checkpointed_frames=checkpointed_frames,
        )


__all__ = [
    "CHECKPOINT_MUTEX_NAME",
    "CheckpointCoordinatorError",
    "CheckpointOwnerBusyError",
    "CheckpointServiceResult",
    "PassiveCheckpointCoordinator",
    "PassiveCheckpointResult",
    "hold_checkpoint_ownership",
]

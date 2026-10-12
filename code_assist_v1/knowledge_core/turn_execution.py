"""Machine-wide, nonblocking execution ownership for a durable canonical turn.

SQLite remains authoritative for acceptance, bindings and the single final
message. The Windows mutex only excludes simultaneous synchronous execution;
OS process death releases it, unlike a process-local pending-request registry.
"""
from contextlib import contextmanager
import ctypes
from hashlib import sha256
import json
import sys
import threading
from uuid import UUID

from .errors import KnowledgeCoreError

TURN_EXECUTION_PROTOCOL = "windows-canonical-turn-v1"
_owned = threading.local()


class TurnExecutionOwnershipError(KnowledgeCoreError):
    code = "TURN_EXECUTION_OWNERSHIP_UNAVAILABLE"
    http_status = 503
    retryable = True


def _name(database_identity: tuple[int, int], conversation_id: str, operation_id: str) -> str:
    if (
        not isinstance(database_identity, tuple) or len(database_identity) != 2
        or any(type(v) is not int or v < 0 for v in database_identity)
    ):
        raise ValueError("Turn ownership requires a verified database file identity.")
    for value in (conversation_id, operation_id):
        if not isinstance(value, str) or str(UUID(value)) != value:
            raise ValueError("Turn ownership requires canonical UUIDs.")
    binding = json.dumps([database_identity, conversation_id, operation_id], separators=(",", ":"))
    return "Global\\CodeAssistCanonicalTurnV1-" + sha256(binding.encode()).hexdigest()


@contextmanager
def hold_turn_execution(database_identity: tuple[int, int], conversation_id: str, operation_id: str):
    name = _name(database_identity, conversation_id, operation_id)
    if sys.platform != "win32":
        raise TurnExecutionOwnershipError("Canonical turn execution requires the approved Windows host.")
    held = getattr(_owned, "names", None)
    if held is None:
        held = _owned.names = set()
    # Windows mutexes are reentrant by thread; nested callbacks must not gain a
    # second executor capability. The OS mutex still gates other threads/processes.
    if name in held:
        yield False
        return
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = (ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p)
    kernel.CreateMutexW.restype = ctypes.c_void_p
    kernel.WaitForSingleObject.argtypes = (ctypes.c_void_p, ctypes.c_uint32)
    kernel.WaitForSingleObject.restype = ctypes.c_uint32
    kernel.ReleaseMutex.argtypes = (ctypes.c_void_p,)
    kernel.ReleaseMutex.restype = ctypes.c_int
    kernel.CloseHandle.argtypes = (ctypes.c_void_p,)
    kernel.CloseHandle.restype = ctypes.c_int
    handle = kernel.CreateMutexW(None, 0, name)
    if not handle:
        raise TurnExecutionOwnershipError("The canonical turn mutex could not be opened.")
    acquired = False
    try:
        result = int(kernel.WaitForSingleObject(handle, 0))
        if result == 0x102:
            yield False
            return
        if result not in {0, 0x80}:
            raise TurnExecutionOwnershipError("The canonical turn mutex could not be acquired.")
        acquired = True
        held.add(name)
        yield True
    finally:
        if acquired:
            held.discard(name)
            released = kernel.ReleaseMutex(handle)
        else:
            released = True
        closed = kernel.CloseHandle(handle)
        if not released or not closed:
            raise TurnExecutionOwnershipError("The canonical turn mutex could not be released.")

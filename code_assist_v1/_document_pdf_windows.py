"""Windows process/job ownership for the optional PDF worker (not a security sandbox).

Launch suspended, assign limits, then resume: venv redirector descendants must
inherit the job too. Only this process's anonymous job/handles are terminated.
"""
import ctypes as c
from ctypes import wintypes as w
import os
import subprocess
import time


class _BasicLimits(c.Structure):
    _fields_ = [("process_time", c.c_longlong), ("job_time", c.c_longlong),
        ("flags", w.DWORD), ("min_working", c.c_size_t), ("max_working", c.c_size_t),
        ("active_limit", w.DWORD), ("affinity", c.c_size_t),
        ("priority", w.DWORD), ("scheduling", w.DWORD)]


class _IO(c.Structure):
    _fields_ = [(name, c.c_ulonglong) for name in
               ("read_ops", "write_ops", "other_ops", "read_bytes", "write_bytes", "other_bytes")]


class _ExtendedLimits(c.Structure):
    _fields_ = [("basic", _BasicLimits), ("io", _IO), ("process_memory", c.c_size_t),
        ("job_memory", c.c_size_t), ("peak_process_memory", c.c_size_t),
        ("peak_job_memory", c.c_size_t)]


class _Accounting(c.Structure):
    _fields_ = [(name, c.c_longlong) for name in ("user", "kernel", "period_user", "period_kernel")] + [
        (name, w.DWORD) for name in ("faults", "total", "active", "terminated")]


class _Startup(c.Structure):
    _fields_ = [("cb", w.DWORD), ("reserved", w.LPWSTR), ("desktop", w.LPWSTR),
        ("title", w.LPWSTR)] + [(name, w.DWORD) for name in
        ("x", "y", "xsize", "ysize", "xcount", "ycount", "fill", "flags")] + [
        ("show", w.WORD), ("reserved_size", w.WORD), ("reserved_ptr", c.c_void_p),
        ("stdin", w.HANDLE), ("stdout", w.HANDLE), ("stderr", w.HANDLE)]


class _StartupEx(c.Structure):
    _fields_ = [("base", _Startup), ("attributes", c.c_void_p)]


class _ProcessInfo(c.Structure):
    _fields_ = [("process", w.HANDLE), ("thread", w.HANDLE),
               ("pid", w.DWORD), ("tid", w.DWORD)]


def _kernel():
    if os.name != "nt":
        raise OSError("PDF job isolation currently requires Windows")
    dll = c.WinDLL("kernel32", use_last_error=True)
    signatures = {
        "CreateJobObjectW": ([c.c_void_p, w.LPCWSTR], w.HANDLE),
        "SetInformationJobObject": ([w.HANDLE, c.c_int, c.c_void_p, w.DWORD], w.BOOL),
        "QueryInformationJobObject": ([w.HANDLE, c.c_int, c.c_void_p, w.DWORD, c.c_void_p], w.BOOL),
        "AssignProcessToJobObject": ([w.HANDLE, w.HANDLE], w.BOOL),
        "TerminateJobObject": ([w.HANDLE, w.UINT], w.BOOL),
        "CloseHandle": ([w.HANDLE], w.BOOL),
        "InitializeProcThreadAttributeList": ([c.c_void_p, w.DWORD, w.DWORD, c.POINTER(c.c_size_t)], w.BOOL),
        "UpdateProcThreadAttribute": ([c.c_void_p, w.DWORD, c.c_size_t, c.c_void_p,
            c.c_size_t, c.c_void_p, c.c_void_p], w.BOOL),
        "DeleteProcThreadAttributeList": ([c.c_void_p], None),
        "CreateProcessW": ([w.LPCWSTR, w.LPWSTR, c.c_void_p, c.c_void_p, w.BOOL,
            w.DWORD, c.c_void_p, w.LPCWSTR, c.POINTER(_StartupEx), c.POINTER(_ProcessInfo)], w.BOOL),
        "ResumeThread": ([w.HANDLE], w.DWORD),
        "GetExitCodeProcess": ([w.HANDLE, c.POINTER(w.DWORD)], w.BOOL),
        "WaitForSingleObject": ([w.HANDLE, w.DWORD], w.DWORD),
        "TerminateProcess": ([w.HANDLE, w.UINT], w.BOOL),
    }
    for name, (args, result) in signatures.items():
        function = getattr(dll, name)
        function.argtypes, function.restype = args, result
    return dll


def _check(value):
    if not value:
        raise c.WinError(c.get_last_error())
    return value


class WindowsPdfProcess:
    """Own one bounded job, the root process handle and three parent pipe ends."""
    def __init__(self, argv, *, cwd, memory_bytes, max_processes, cleanup_seconds=5):
        import msvcrt
        self.k = _kernel()
        self.job = self.process = None
        self.pid = 0
        self.stdin = self.stdout = self.stderr = None
        self._assigned = False
        descriptors = []
        attributes = None
        attributes_initialized = False
        info = _ProcessInfo()
        try:
            self.job = _check(self.k.CreateJobObjectW(None, None))
            limits = _ExtendedLimits()
            # ACTIVE_PROCESS | PROCESS_MEMORY | JOB_MEMORY | KILL_ON_JOB_CLOSE.
            # No BREAKAWAY flags, no inherited job handle.
            limits.basic.flags = 0x8 | 0x100 | 0x200 | 0x2000
            limits.basic.active_limit = max_processes
            limits.process_memory = limits.job_memory = memory_bytes
            _check(self.k.SetInformationJobObject(self.job, 9, c.byref(limits), c.sizeof(limits)))
            for _ in range(3):
                descriptors.extend(os.pipe())
            in_read, in_write, out_read, out_write, err_read, err_write = descriptors
            child_fds = (in_read, out_write, err_write)
            handles = (w.HANDLE * 3)(*(msvcrt.get_osfhandle(fd) for fd in child_fds))
            for fd in child_fds:
                os.set_inheritable(fd, True)
            size = c.c_size_t()
            self.k.InitializeProcThreadAttributeList(None, 1, 0, c.byref(size))
            attributes = c.create_string_buffer(size.value)
            _check(self.k.InitializeProcThreadAttributeList(attributes, 1, 0, c.byref(size)))
            attributes_initialized = True
            # PROC_THREAD_ATTRIBUTE_HANDLE_LIST; no other parent handles inherited.
            _check(self.k.UpdateProcThreadAttribute(attributes, 0, 0x20002, handles,
                c.sizeof(handles), None, None))
            startup = _StartupEx()
            startup.base.cb = c.sizeof(startup)
            startup.base.flags = 0x100  # STARTF_USESTDHANDLES
            startup.base.stdin, startup.base.stdout, startup.base.stderr = handles
            startup.attributes = c.cast(attributes, c.c_void_p)
            environment = {k: v for k, v in os.environ.items()
                           if k.upper() in {"SYSTEMROOT", "WINDIR", "TEMP", "TMP"}}
            environment_block = c.create_unicode_buffer("\0".join(
                f"{k}={v}" for k, v in sorted(environment.items())) + "\0")
            command = c.create_unicode_buffer(subprocess.list2cmdline(argv))
            # CREATE_SUSPENDED | CREATE_NO_WINDOW | EXTENDED_STARTUPINFO_PRESENT |
            # CREATE_UNICODE_ENVIRONMENT. Assignment happens before ANY child code.
            _check(self.k.CreateProcessW(argv[0], command, None, None, True,
                0x4 | 0x08000000 | 0x00080000 | 0x400, environment_block, cwd,
                c.byref(startup), c.byref(info)))
            self.process, self.pid = info.process, info.pid
            for fd in child_fds:
                os.close(fd)
                descriptors.remove(fd)
            _check(self.k.AssignProcessToJobObject(self.job, self.process))
            self._assigned = True
            if self.k.ResumeThread(info.thread) == 0xFFFFFFFF:
                raise c.WinError(c.get_last_error())
            self.stdin = os.fdopen(in_write, "wb", buffering=0)
            descriptors.remove(in_write)
            self.stdout = os.fdopen(out_read, "rb", buffering=0)
            descriptors.remove(out_read)
            self.stderr = os.fdopen(err_read, "rb", buffering=0)
            descriptors.remove(err_read)
        except BaseException:
            try:
                try:
                    self.stop()
                finally:
                    if self.process:
                        # Even a Job termination error must not bypass root
                        # observation: the child may not have been assigned.
                        self.wait_stopped(cleanup_seconds)
            finally:
                self.close()
            raise
        finally:
            if info.thread:
                self.k.CloseHandle(info.thread)
            if attributes_initialized:
                self.k.DeleteProcThreadAttributeList(attributes)
            for fd in descriptors:
                os.close(fd)

    def poll(self):
        status = self.k.WaitForSingleObject(self.process, 0)
        if status == 0x102:  # WAIT_TIMEOUT, including a legitimate exit code259.
            return None
        if status != 0:
            raise c.WinError(c.get_last_error())
        code = w.DWORD()
        _check(self.k.GetExitCodeProcess(self.process, c.byref(code)))
        return code.value

    def metrics(self):
        limits, accounting = _ExtendedLimits(), _Accounting()
        _check(self.k.QueryInformationJobObject(self.job, 9, c.byref(limits), c.sizeof(limits), None))
        _check(self.k.QueryInformationJobObject(self.job, 1, c.byref(accounting), c.sizeof(accounting), None))
        return {"peak_committed_bytes": limits.peak_job_memory,
                "total_processes": accounting.total, "active_processes": accounting.active}

    def stop(self):
        try:
            if self.job:
                _check(self.k.TerminateJobObject(self.job, 125))
        finally:
            # An empty Job cannot kill a child whose assignment failed. Do not
            # skip this attempt when terminating that Job also reports an error.
            if self.process and not self._assigned:
                _check(self.k.TerminateProcess(self.process, 125))

    def wait_stopped(self, timeout):
        until = time.monotonic() + timeout
        while time.monotonic() < until:
            if self.metrics()["active_processes"] == 0 and self.poll() is not None:
                return
            time.sleep(0.01)
        raise OSError("PDF job cleanup did not finish within its deadline")

    def close_job(self):
        if self.job:
            self.k.CloseHandle(self.job)
            self.job = None

    def close(self):
        # Kill backstop before closing pipe handles.
        self.close_job()
        for stream in (self.stdin, self.stdout, self.stderr):
            if stream is not None:
                stream.close()
        if self.process:
            self.k.CloseHandle(self.process)
            self.process = None

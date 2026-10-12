"""
Lightweight per-user background job manager.

This is intentionally small and dependency-free. It gives long-running Flask
routes a safe escape hatch without changing their existing synchronous API
contract.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable

from code_assist_v1.auth import safe_user_id
from code_assist_v1.config import USER_DATA_DIR


JOB_STATUSES = {"queued", "running", "done", "failed", "cancelled"}
MAX_JOB_WORKERS = max(1, int(os.environ.get("CODE_ASSIST_JOB_WORKERS", "2") or "2"))
MAX_ERROR_CHARS = 1200


class JobCancelled(RuntimeError):
    pass


def _now() -> int:
    return int(time.time())


def redact_sensitive(value: Any) -> str:
    text = str(value or "")
    text = re.sub(r"Bearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [REDACTED]", text, flags=re.I)
    text = re.sub(r"(?i)(api[_-]?key|token|password|secret)=([^&\s]+)", r"\1=[REDACTED]", text)
    text = re.sub(r"https?://([^/\s]*redacted[^/\s]*)[^\s)]*", r"http://[REDACTED]", text, flags=re.I)
    text = re.sub(r"\b[\w.-]*redacted[\w.-]*\b", "[REDACTED]", text, flags=re.I)
    return text[:MAX_ERROR_CHARS]


def _job_root(user_id: str) -> str:
    uid = safe_user_id(user_id)
    if not uid:
        raise ValueError("authenticated user required")
    path = os.path.join(USER_DATA_DIR, uid, "jobs")
    os.makedirs(path, exist_ok=True)
    return path


def _job_path(user_id: str, job_id: str) -> str:
    return os.path.join(_job_root(user_id), f"{job_id}.json")


def _atomic_write_json(path: str, data: dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
    os.replace(tmp, path)


class JobContext:
    def __init__(self, manager: "JobManager", job_id: str):
        self._manager = manager
        self.job_id = job_id

    def update(self, progress: int | None = None, message: str | None = None, **fields: Any) -> dict[str, Any]:
        return self._manager.update_job(self.job_id, progress=progress, message=message, **fields)

    def is_cancelled(self) -> bool:
        return self._manager.is_cancel_requested(self.job_id)

    def raise_if_cancelled(self) -> None:
        if self.is_cancelled():
            raise JobCancelled("job cancelled")


JobTarget = Callable[[JobContext], Any]


class JobManager:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._jobs: dict[str, dict[str, Any]] = {}
        self._futures: dict[str, Future] = {}
        self._executor = ThreadPoolExecutor(max_workers=MAX_JOB_WORKERS, thread_name_prefix="code-assist-job")

    def submit(
        self,
        *,
        user_id: str,
        kind: str,
        target: JobTarget,
        session_id: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        from code_assist_v1.model_runtime import assert_legacy_background_jobs_allowed

        assert_legacy_background_jobs_allowed()
        job = self.create_job(user_id=user_id, kind=kind, session_id=session_id, metadata=metadata)
        future = self._executor.submit(self._run, job["job_id"], target)
        with self._lock:
            self._futures[job["job_id"]] = future
        return job

    def create_job(
        self,
        *,
        user_id: str,
        kind: str,
        session_id: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        from code_assist_v1.model_runtime import assert_legacy_background_jobs_allowed

        assert_legacy_background_jobs_allowed()
        uid = safe_user_id(user_id)
        if not uid:
            raise ValueError("authenticated user required")
        now = _now()
        job_id = f"job-{now}-{uuid.uuid4().hex[:10]}"
        job = {
            "job_id": job_id,
            "user_id": uid,
            "session_id": str(session_id or ""),
            "kind": str(kind or "task"),
            "status": "queued",
            "progress": 0,
            "message": "queued",
            "error": "",
            "result": None,
            "metadata": metadata or {},
            "cancel_requested": False,
            "created_at": now,
            "updated_at": now,
        }
        with self._lock:
            self._jobs[job_id] = job
            self._persist(job)
        return dict(job)

    def _run(self, job_id: str, target: JobTarget) -> None:
        ctx = JobContext(self, job_id)
        try:
            ctx.raise_if_cancelled()
            self.update_job(job_id, status="running", progress=1, message="running")
            result = target(ctx)
            self.update_job(
                job_id,
                status="done",
                progress=100,
                message="done",
                result=result,
                error="",
            )
        except JobCancelled as e:
            self.update_job(job_id, status="cancelled", message=str(e), error="")
        except Exception as e:
            self.update_job(job_id, status="failed", message="failed", error=redact_sensitive(e))

    def _persist(self, job: dict[str, Any]) -> None:
        _atomic_write_json(_job_path(job["user_id"], job["job_id"]), job)

    def update_job(
        self,
        job_id: str,
        *,
        status: str | None = None,
        progress: int | None = None,
        message: str | None = None,
        error: Any | None = None,
        result: Any = None,
        **fields: Any,
    ) -> dict[str, Any]:
        with self._lock:
            job = self._jobs.get(job_id) or self._load_by_id(job_id)
            if not job:
                raise KeyError(job_id)
            if status:
                if status not in JOB_STATUSES:
                    raise ValueError(f"invalid job status: {status}")
                job["status"] = status
            if progress is not None:
                job["progress"] = max(0, min(100, int(progress)))
            if message is not None:
                job["message"] = str(message)
            if error is not None:
                job["error"] = redact_sensitive(error)
            if result is not None:
                job["result"] = result
            for key, value in fields.items():
                if key:
                    job[key] = value
            job["updated_at"] = _now()
            self._jobs[job_id] = job
            self._persist(job)
            return dict(job)

    def get_job(self, job_id: str, user_id: str | None = None, include_any_user: bool = False) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                job = self._load_by_id(job_id, user_id=user_id if not include_any_user else None)
            if not job:
                return None
            if not include_any_user and safe_user_id(user_id) != safe_user_id(job.get("user_id")):
                return None
            return dict(job)

    def list_jobs(self, user_id: str, include_all: bool = False, limit: int = 100) -> list[dict[str, Any]]:
        uid = safe_user_id(user_id)
        if not uid:
            return []
        items: list[dict[str, Any]] = []
        roots: list[tuple[str, str]] = []
        if include_all:
            try:
                roots = [
                    (name, os.path.join(USER_DATA_DIR, name, "jobs"))
                    for name in os.listdir(USER_DATA_DIR)
                    if safe_user_id(name)
                ]
            except OSError:
                roots = []
        else:
            roots = [(uid, _job_root(uid))]
        for _, root in roots:
            if not os.path.isdir(root):
                continue
            for name in os.listdir(root):
                if not name.endswith(".json"):
                    continue
                path = os.path.join(root, name)
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        job = json.load(f)
                    if isinstance(job, dict):
                        items.append(job)
                except Exception:
                    continue
        with self._lock:
            merged = {item.get("job_id"): item for item in items if item.get("job_id")}
            for job in self._jobs.values():
                if include_all or safe_user_id(job.get("user_id")) == uid:
                    merged[job["job_id"]] = dict(job)
        out = list(merged.values())
        out.sort(key=lambda item: int(item.get("created_at") or 0), reverse=True)
        return out[: max(1, min(500, int(limit or 100)))]

    def cancel_job(self, job_id: str, user_id: str, include_any_user: bool = False) -> dict[str, Any] | None:
        with self._lock:
            job = self.get_job(job_id, user_id=user_id, include_any_user=include_any_user)
            if not job:
                return None
            if job.get("status") in {"done", "failed", "cancelled"}:
                return job
            job["cancel_requested"] = True
            job["message"] = "cancel requested"
            if job.get("status") == "queued":
                future = self._futures.get(job_id)
                if future and future.cancel():
                    job["status"] = "cancelled"
                    job["progress"] = 0
                    job["message"] = "cancelled"
            job["updated_at"] = _now()
            self._jobs[job_id] = job
            self._persist(job)
            return dict(job)

    def is_cancel_requested(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id) or self._load_by_id(job_id)
            return bool(job and job.get("cancel_requested"))

    def _load_by_id(self, job_id: str, user_id: str | None = None) -> dict[str, Any] | None:
        users: list[str] = []
        if user_id:
            uid = safe_user_id(user_id)
            if uid:
                users = [uid]
        else:
            try:
                users = [name for name in os.listdir(USER_DATA_DIR) if safe_user_id(name)]
            except OSError:
                users = []
        for uid in users:
            path = _job_path(uid, job_id)
            if not os.path.isfile(path):
                continue
            try:
                with open(path, "r", encoding="utf-8") as f:
                    job = json.load(f)
                if isinstance(job, dict):
                    self._jobs[job_id] = job
                    return job
            except Exception:
                continue
        return None


manager = JobManager()

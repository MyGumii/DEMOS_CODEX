"""Explicit P7 durable worker loop with central inference ownership.

The loop is never started by importing or constructing this module.  Callers
must select the exact ``worker`` process role and invoke :meth:`run_once` or
:meth:`serve`.  A machine-wide Windows named mutex prevents a second service,
deployment, or database configuration from becoming a concurrent local GGUF
owner on the same server.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from collections.abc import Callable
from typing import Protocol

from code_assist_v1.model_runtime import hold_inference_ownership

from .repositories.jobs import ClaimResult, RecoveryResult
from .worker import DurableWorkKernel, JobRunResult


_CLAIM_OWNER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}$", re.ASCII)


class DurableInferenceWorkerError(RuntimeError):
    code = "DURABLE_INFERENCE_WORKER_ERROR"


class ClaimHeartbeatError(DurableInferenceWorkerError):
    code = "JOB_HEARTBEAT_FAILED"


class StopSignal(Protocol):
    def is_set(self) -> bool: ...

    def wait(self, timeout: float | None = None) -> bool: ...


@dataclass(frozen=True, slots=True)
class DurableInferenceWorkerConfig:
    claim_owner: str
    lease_ms: int = 300_000
    heartbeat_interval_ms: int = 30_000
    idle_poll_ms: int = 100
    maximum_idle_poll_ms: int = 5_000

    def __post_init__(self) -> None:
        if (
            not isinstance(self.claim_owner, str)
            or _CLAIM_OWNER_RE.fullmatch(self.claim_owner) is None
        ):
            raise ValueError("claim_owner has an invalid durable worker shape.")
        for name in (
            "lease_ms",
            "heartbeat_interval_ms",
            "idle_poll_ms",
            "maximum_idle_poll_ms",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive integer.")
        if self.lease_ms < 300:
            raise ValueError("lease_ms must be at least 300 milliseconds.")
        if self.heartbeat_interval_ms * 3 >= self.lease_ms:
            raise ValueError(
                "heartbeat_interval_ms must be less than one third of lease_ms."
            )
        if self.maximum_idle_poll_ms < self.idle_poll_ms:
            raise ValueError(
                "maximum_idle_poll_ms cannot be lower than idle_poll_ms."
            )


@dataclass(frozen=True, slots=True)
class WorkerIterationResult:
    recovery: RecoveryResult
    claim: ClaimResult | None
    run: JobRunResult | None
    heartbeat_count: int

    @property
    def idle(self) -> bool:
        return self.claim is None


@dataclass(frozen=True, slots=True)
class WorkerServiceResult:
    iterations: int
    claimed: int
    idle_polls: int
    heartbeat_count: int
    recovered_retry: int
    recovered_dead: int
    recovered_cancelled: int
    terminal_counts: tuple[tuple[str, int], ...]


class _HeartbeatKeeper:
    def __init__(
        self,
        kernel: DurableWorkKernel,
        claim: ClaimResult,
        config: DurableInferenceWorkerConfig,
    ) -> None:
        self._kernel = kernel
        self._claim = claim
        self._config = config
        self._stop = threading.Event()
        self._count = 0
        self._error: Exception | None = None
        self._thread = threading.Thread(
            target=self._run,
            name=f"job-heartbeat-{claim.job.id[:8]}",
            daemon=False,
        )

    @property
    def count(self) -> int:
        return self._count

    @property
    def error(self) -> Exception | None:
        return self._error

    def start(self) -> None:
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=max(1.0, self._config.lease_ms / 1000))
        if self._thread.is_alive():
            raise ClaimHeartbeatError(
                "The durable claim heartbeat did not stop within its lease bound."
            )

    def _run(self) -> None:
        interval = self._config.heartbeat_interval_ms / 1000
        while not self._stop.wait(interval):
            try:
                self._kernel.renew_claim(
                    self._claim.fence,
                    lease_ms=self._config.lease_ms,
                )
                self._count += 1
            except Exception as exc:  # commit remains protected by the DB fence
                self._error = exc
                return


class DurableInferenceWorker:
    """Single-concurrency durable scheduler with handler and actor fairness."""

    __slots__ = (
        "_before_poll",
        "_config",
        "_kernel",
    )

    def __init__(
        self,
        kernel: DurableWorkKernel,
        config: DurableInferenceWorkerConfig,
        *,
        before_poll: Callable[[], object] | None = None,
    ) -> None:
        if not isinstance(kernel, DurableWorkKernel):
            raise TypeError("DurableInferenceWorker requires a DurableWorkKernel.")
        if not isinstance(config, DurableInferenceWorkerConfig):
            raise TypeError(
                "DurableInferenceWorker requires DurableInferenceWorkerConfig."
            )
        if before_poll is not None and not callable(before_poll):
            raise TypeError("before_poll must be callable or None.")
        self._kernel = kernel
        self._config = config
        self._before_poll = before_poll

    @property
    def config(self) -> DurableInferenceWorkerConfig:
        return self._config

    def run_once(self) -> WorkerIterationResult:
        """Acquire central ownership and execute at most one durable job."""

        with hold_inference_ownership(self._kernel.database_path):
            self._kernel.verify_registry_coverage()
            return self._run_once_owned()

    def serve(
        self,
        stop: StopSignal,
        *,
        maximum_jobs: int | None = None,
    ) -> WorkerServiceResult:
        """Poll until stopped; one owned process and one job execute at a time."""

        if not callable(getattr(stop, "is_set", None)) or not callable(
            getattr(stop, "wait", None)
        ):
            raise TypeError("stop must expose is_set() and wait().")
        if maximum_jobs is not None and (
            isinstance(maximum_jobs, bool)
            or not isinstance(maximum_jobs, int)
            or maximum_jobs < 1
        ):
            raise ValueError("maximum_jobs must be a positive integer or None.")

        iterations = claimed = idle_polls = heartbeat_count = 0
        recovered_retry = recovered_dead = recovered_cancelled = 0
        terminal: dict[str, int] = {}
        idle_delay = self._config.idle_poll_ms
        with hold_inference_ownership(self._kernel.database_path):
            self._kernel.verify_registry_coverage()
            while not stop.is_set():
                result = self._run_once_owned()
                iterations += 1
                recovered_retry += len(result.recovery.retried_ids)
                recovered_dead += len(result.recovery.dead_ids)
                recovered_cancelled += len(result.recovery.cancelled_ids)
                heartbeat_count += result.heartbeat_count
                if result.idle:
                    idle_polls += 1
                    stop.wait(idle_delay / 1000)
                    idle_delay = min(
                        self._config.maximum_idle_poll_ms,
                        idle_delay * 2,
                    )
                    continue
                idle_delay = self._config.idle_poll_ms
                claimed += 1
                if result.run is not None:
                    state = result.run.job.state
                    terminal[state] = terminal.get(state, 0) + 1
                if maximum_jobs is not None and claimed >= maximum_jobs:
                    break
        return WorkerServiceResult(
            iterations=iterations,
            claimed=claimed,
            idle_polls=idle_polls,
            heartbeat_count=heartbeat_count,
            recovered_retry=recovered_retry,
            recovered_dead=recovered_dead,
            recovered_cancelled=recovered_cancelled,
            terminal_counts=tuple(sorted(terminal.items())),
        )

    def _run_once_owned(self) -> WorkerIterationResult:
        if self._before_poll is not None:
            # The maintenance hook is deliberately bounded and runs while the
            # process owns central inference.  It may seed/materialize durable
            # work, but it must never perform model or network work.
            self._before_poll()
        recovery = self._kernel.recover_expired()
        claim = self._claim_fair()
        if claim is None:
            return WorkerIterationResult(recovery, None, None, 0)
        keeper = _HeartbeatKeeper(self._kernel, claim, self._config)
        keeper.start()
        run: JobRunResult | None = None
        try:
            run = self._kernel.run_claim(claim)
        finally:
            keeper.close()
        if keeper.error is not None and (
            run is None
            or run.job.state not in {"succeeded", "failed", "cancelled", "dead", "retry"}
        ):
            raise ClaimHeartbeatError(
                "The durable claim heartbeat failed before terminal convergence."
            ) from keeper.error
        return WorkerIterationResult(recovery, claim, run, keeper.count)

    def _claim_fair(self) -> ClaimResult | None:
        return self._kernel.claim_next(
            claim_owner=self._config.claim_owner,
            lease_ms=self._config.lease_ms,
            handler_keys=self._kernel.registry.keys,
            durable_fairness=True,
        )


__all__ = [
    "ClaimHeartbeatError",
    "DurableInferenceWorker",
    "DurableInferenceWorkerConfig",
    "DurableInferenceWorkerError",
    "WorkerIterationResult",
    "WorkerServiceResult",
]

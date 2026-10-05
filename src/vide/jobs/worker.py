"""Worker pool that drains the job queue.

Handlers register by job type and receive a session plus the job. A handler
returning normally completes the job; raising fails it, with a retry scheduled
while attempts remain.

Concurrency is capped two ways: a global slot count per worker
process, and an optional per-type cap so one slow provider cannot starve
everything else. Both matter once image generation lands — a provider that
takes minutes per call will otherwise hold every slot.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import socket
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

import structlog

from vide.db import session_scope
from vide.jobs import queue
from vide.models.ops import Job

log = structlog.get_logger(__name__)

#: A handler takes the job payload and returns a result dict for the log.
Handler = Callable[[dict], Awaitable[dict | None]]

_HANDLERS: dict[str, Handler] = {}
_TYPE_LIMITS: dict[str, int] = {}


def handler(job_type: str, *, max_concurrent: int | None = None):
    """Register a coroutine as the handler for a job type.

    ``max_concurrent`` caps how many of this type run at once across this
    process — the lever for respecting a provider's rate limit.
    """

    def decorator(func: Handler) -> Handler:
        if job_type in _HANDLERS:
            raise ValueError(f"handler for {job_type!r} is already registered")
        _HANDLERS[job_type] = func
        if max_concurrent is not None:
            _TYPE_LIMITS[job_type] = max_concurrent
        return func

    return decorator


def registered_types() -> list[str]:
    return sorted(_HANDLERS)


@dataclass(slots=True)
class WorkerConfig:
    #: Total jobs in flight in this process.
    concurrency: int = 8
    #: Seconds to wait when the queue is empty before looking again.
    idle_sleep: float = 2.0
    #: Restrict this worker to certain job types. Empty means all registered.
    types: list[str] = field(default_factory=list)
    worker_id: str = field(
        default_factory=lambda: f"{socket.gethostname()}:{os.getpid()}"
    )


class Worker:
    def __init__(self, config: WorkerConfig | None = None) -> None:
        self.config = config or WorkerConfig()
        self._stopping = asyncio.Event()
        self._slots = asyncio.Semaphore(self.config.concurrency)
        self._type_slots = {
            job_type: asyncio.Semaphore(limit)
            for job_type, limit in _TYPE_LIMITS.items()
        }
        self._running: set[asyncio.Task] = set()

    # -- lifecycle ------------------------------------------------------------

    def stop(self) -> None:
        """Stop claiming new work. In-flight jobs are allowed to finish."""
        if not self._stopping.is_set():
            log.info("worker.stopping", worker=self.config.worker_id)
            self._stopping.set()

    async def run(self) -> None:
        types = self.config.types or registered_types()
        if not types:
            raise RuntimeError("no job handlers registered")

        log.info(
            "worker.started",
            worker=self.config.worker_id,
            concurrency=self.config.concurrency,
            types=types,
        )

        while not self._stopping.is_set():
            await self._slots.acquire()
            if self._stopping.is_set():
                self._slots.release()
                break

            job_id, job_type, payload = await asyncio.to_thread(self._claim_one, types)
            if job_id is None:
                self._slots.release()
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(
                        self._stopping.wait(), timeout=self.config.idle_sleep
                    )
                continue

            task = asyncio.create_task(self._run_job(job_id, job_type, payload))
            self._running.add(task)
            task.add_done_callback(self._running.discard)

        if self._running:
            log.info("worker.draining", in_flight=len(self._running))
            await asyncio.gather(*self._running, return_exceptions=True)
        log.info("worker.stopped", worker=self.config.worker_id)

    # -- internals ------------------------------------------------------------

    def _claim_one(
        self, types: list[str]
    ) -> tuple[uuid.UUID | None, str | None, dict | None]:
        """Claim in a thread — the DB session is synchronous."""
        with session_scope() as session:
            jobs = queue.claim(session, self.config.worker_id, types=types, limit=1)
            if not jobs:
                return None, None, None
            job = jobs[0]
            # Detach the values; the ORM object does not outlive this session.
            return job.id, job.type, dict(job.payload)

    async def _run_job(self, job_id: uuid.UUID, job_type: str, payload: dict) -> None:
        type_slot = self._type_slots.get(job_type)
        try:
            if type_slot is not None:
                await type_slot.acquire()
            try:
                await self._execute(job_id, job_type, payload)
            finally:
                if type_slot is not None:
                    type_slot.release()
        finally:
            self._slots.release()

    async def _execute(self, job_id: uuid.UUID, job_type: str, payload: dict) -> None:
        func = _HANDLERS.get(job_type)
        if func is None:
            await asyncio.to_thread(
                self._finish, job_id, None, f"no handler registered for {job_type!r}", False
            )
            return

        bound = log.bind(job=str(job_id), type=job_type)
        try:
            result = await func(payload)
            await asyncio.to_thread(self._finish, job_id, result or {}, None, False)
            bound.info("job.succeeded")
        except asyncio.CancelledError:
            await asyncio.to_thread(
                self._finish, job_id, None, "cancelled during shutdown", True
            )
            raise
        except Exception as exc:  # noqa: BLE001 — every failure lands on the row
            await asyncio.to_thread(self._finish, job_id, None, repr(exc), True)
            bound.warning("job.failed", error=str(exc)[:300])

    @staticmethod
    def _finish(
        job_id: uuid.UUID, result: dict | None, error: str | None, retry: bool
    ) -> None:
        with session_scope() as session:
            job = session.get(Job, job_id)
            if job is None:
                return
            if error is None:
                queue.complete(session, job, result)
            else:
                queue.fail(session, job, error, retry=retry)


async def run_worker(config: WorkerConfig | None = None) -> None:
    """Run a worker until SIGINT or SIGTERM, then drain."""
    worker = Worker(config)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, worker.stop)
    await worker.run()

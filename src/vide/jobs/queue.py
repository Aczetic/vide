"""Postgres-backed work queue.

Stands in for a durable workflow engine for now, when the first real
generate → review → gate loop exists and Temporal earns its keep. The surface
is deliberately narrow — enqueue, claim, complete, fail, fan out — so swapping
the implementation later does not reach into pipeline code.

Claiming uses ``FOR UPDATE SKIP LOCKED``, so workers scale horizontally without
coordinating and two workers never take the same row.

**Gates are not blocked jobs.** calls a human gate a durable wait, but
parking a row in ``running`` for three days while a client decides would tie up
a lease and lie about what is executing. Instead the job *completes*, the
element moves to ``awaiting_gate``, and the gate decision enqueues whatever
comes next. The wait lives in element state, where the UI can see it.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from vide.models.enums import JobStatus
from vide.models.ops import Job

log = structlog.get_logger(__name__)

#: A claimed job whose worker has gone silent for longer than this is fair game.
#: Long enough to outlast a slow provider call, short enough that a crashed
#: worker does not strand work for an hour.
LEASE_SECONDS = 900

#: Retry backoff per attempt, in seconds. Index is the attempt number.
BACKOFF = (5, 30, 120, 600)


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass(slots=True)
class EnqueueSpec:
    type: str
    payload: dict[str, Any]
    project_id: uuid.UUID | None = None
    priority: int = 100
    max_attempts: int = 3
    run_after: datetime | None = None
    parent_job_id: uuid.UUID | None = None
    #: Re-enqueuing the same unit of work is a no-op. Makes retries and
    #: pipeline restarts safe without deduping at every call site.
    idempotency_key: str | None = None


def enqueue(session: Session, spec: EnqueueSpec) -> Job | None:
    """Add one job. Returns None when an idempotency key already exists."""
    if spec.idempotency_key:
        existing = session.scalar(
            select(Job).where(Job.idempotency_key == spec.idempotency_key)
        )
        if existing is not None:
            return None

    job = Job(
        type=spec.type,
        payload=spec.payload,
        project_id=spec.project_id,
        priority=spec.priority,
        max_attempts=spec.max_attempts,
        run_after=spec.run_after,
        parent_job_id=spec.parent_job_id,
        idempotency_key=spec.idempotency_key,
        status=JobStatus.PENDING,
    )
    session.add(job)
    session.flush()
    return job


def fan_out(session: Session, parent: Job, specs: list[EnqueueSpec]) -> list[Job]:
    """Spawn one child per element (principle 2).

    Characters, locations, props and scenes all fan out this way. Each child is
    independent — one failing does not hold up its siblings, because the whole
    point is that a character clearing its gate moves on without waiting for the
    rest.
    """
    children = []
    for spec in specs:
        spec.parent_job_id = parent.id
        spec.project_id = spec.project_id or parent.project_id
        child = enqueue(session, spec)
        if child is not None:
            children.append(child)
    log.info(
        "jobs.fan_out", parent=str(parent.id), type=parent.type, children=len(children)
    )
    return children


def claim(
    session: Session,
    worker_id: str,
    *,
    types: list[str] | None = None,
    limit: int = 1,
) -> list[Job]:
    """Take up to ``limit`` runnable jobs.

    Runnable means pending and due, or claimed by a worker whose lease expired.
    SKIP LOCKED lets other workers move past rows this transaction holds.
    """
    stale_before = _now() - timedelta(seconds=LEASE_SECONDS)

    conditions = [
        or_(
            Job.status == JobStatus.PENDING,
            (Job.status == JobStatus.RUNNING) & (Job.claimed_at < stale_before),
        ),
        or_(Job.run_after.is_(None), Job.run_after <= _now()),
    ]
    if types:
        conditions.append(Job.type.in_(types))

    rows = session.scalars(
        select(Job)
        .where(*conditions)
        .order_by(Job.priority, Job.created_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    ).all()

    claimed = []
    for job in rows:
        if job.status == JobStatus.RUNNING:
            log.warning(
                "jobs.lease_reclaimed",
                job=str(job.id),
                type=job.type,
                previous_worker=job.claimed_by,
            )
        job.status = JobStatus.RUNNING
        job.claimed_by = worker_id
        job.claimed_at = _now()
        job.started_at = job.started_at or _now()
        job.attempts += 1
        claimed.append(job)

    session.flush()
    return claimed


def complete(session: Session, job: Job, result: dict[str, Any] | None = None) -> None:
    job.status = JobStatus.SUCCEEDED
    job.result = result or {}
    job.finished_at = _now()
    job.error = None
    session.flush()


def fail(session: Session, job: Job, error: str, *, retry: bool = True) -> None:
    """Record a failure, scheduling a retry while attempts remain."""
    job.error = error[:4000]
    if retry and job.attempts < job.max_attempts:
        delay = BACKOFF[min(job.attempts - 1, len(BACKOFF) - 1)]
        job.status = JobStatus.PENDING
        job.run_after = _now() + timedelta(seconds=delay)
        job.claimed_by = None
        job.claimed_at = None
        log.warning(
            "jobs.retry_scheduled",
            job=str(job.id),
            type=job.type,
            attempt=job.attempts,
            max_attempts=job.max_attempts,
            in_seconds=delay,
        )
    else:
        job.status = JobStatus.FAILED
        job.finished_at = _now()
        # Attempt budgets escalate to a human rather than looping forever.
        log.error(
            "jobs.exhausted",
            job=str(job.id),
            type=job.type,
            attempts=job.attempts,
            error=error[:200],
        )
    session.flush()


def children_pending(session: Session, parent_id: uuid.UUID) -> int:
    """How many children of a fan-out are still in flight."""
    return (
        session.scalar(
            select(func.count())
            .select_from(Job)
            .where(
                Job.parent_job_id == parent_id,
                Job.status.in_([JobStatus.PENDING, JobStatus.RUNNING]),
            )
        )
        or 0
    )


def stats(session: Session, project_id: uuid.UUID | None = None) -> dict[str, int]:
    """Counts by status — what the header's queue indicator reads."""
    query = select(Job.status, func.count()).group_by(Job.status)
    if project_id is not None:
        query = query.where(Job.project_id == project_id)
    return {str(status): count for status, count in session.execute(query)}


def reset_stuck(session: Session) -> int:
    """Return leases held by dead workers to the pool. Safe to run any time."""
    stale_before = _now() - timedelta(seconds=LEASE_SECONDS)
    result = session.execute(
        update(Job)
        .where(Job.status == JobStatus.RUNNING, Job.claimed_at < stale_before)
        .values(status=JobStatus.PENDING, claimed_by=None, claimed_at=None)
    )
    session.flush()
    return result.rowcount or 0

"""Queue and worker behaviour, against the real database.

These run against the local Postgres because the parts worth testing — SKIP
LOCKED, lease reclaim, transactional claim — are database behaviour. A mocked
session would test nothing.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete

from vide.db import session_scope
from vide.jobs import queue
from vide.jobs.queue import EnqueueSpec
from vide.models.enums import JobStatus
from vide.models.ops import Job


@pytest.fixture
def clean_jobs():
    """Remove only this test's jobs, keyed by a unique type prefix."""
    marker = f"test_{uuid.uuid4().hex[:8]}"
    yield marker
    with session_scope() as session:
        session.execute(delete(Job).where(Job.type.like(f"{marker}%")))


def test_enqueue_and_claim(clean_jobs):
    with session_scope() as session:
        job = queue.enqueue(session, EnqueueSpec(type=clean_jobs, payload={"n": 1}))
        assert job is not None
        job_id = job.id

    with session_scope() as session:
        claimed = queue.claim(session, "worker-a", types=[clean_jobs])
        assert [j.id for j in claimed] == [job_id]
        assert claimed[0].status == JobStatus.RUNNING
        assert claimed[0].claimed_by == "worker-a"
        assert claimed[0].attempts == 1


def test_claim_is_exclusive(clean_jobs):
    """Two workers must never take the same row."""
    with session_scope() as session:
        for i in range(6):
            queue.enqueue(session, EnqueueSpec(type=clean_jobs, payload={"n": i}))

    taken: list[uuid.UUID] = []
    with session_scope() as a, session_scope() as b:
        got_a = queue.claim(a, "worker-a", types=[clean_jobs], limit=3)
        got_b = queue.claim(b, "worker-b", types=[clean_jobs], limit=3)
        taken += [j.id for j in got_a] + [j.id for j in got_b]

    assert len(taken) == 6
    assert len(set(taken)) == 6, "a job was claimed twice"


def test_idempotency_key_dedupes(clean_jobs):
    key = f"{clean_jobs}:unique"
    with session_scope() as session:
        first = queue.enqueue(
            session, EnqueueSpec(type=clean_jobs, payload={}, idempotency_key=key)
        )
        second = queue.enqueue(
            session, EnqueueSpec(type=clean_jobs, payload={}, idempotency_key=key)
        )
    assert first is not None
    assert second is None, "the same unit of work was enqueued twice"


def test_failure_schedules_retry_then_exhausts(clean_jobs):
    with session_scope() as session:
        job = queue.enqueue(
            session, EnqueueSpec(type=clean_jobs, payload={}, max_attempts=2)
        )
        job_id = job.id

    # First attempt fails -> back to pending, with a delay.
    with session_scope() as session:
        job = queue.claim(session, "w", types=[clean_jobs])[0]
        queue.fail(session, job, "boom")
        assert job.status == JobStatus.PENDING
        assert job.run_after is not None and job.run_after > datetime.now(UTC)

    # Not yet due, so it is not claimable.
    with session_scope() as session:
        assert queue.claim(session, "w", types=[clean_jobs]) == []

    # Make it due, fail again -> budget exhausted, escalate rather than loop.
    with session_scope() as session:
        session.get(Job, job_id).run_after = datetime.now(UTC) - timedelta(seconds=1)

    with session_scope() as session:
        job = queue.claim(session, "w", types=[clean_jobs])[0]
        queue.fail(session, job, "boom again")
        assert job.status == JobStatus.FAILED
        assert job.attempts == 2


def test_expired_lease_is_reclaimed(clean_jobs):
    """A crashed worker must not strand its work."""
    with session_scope() as session:
        job = queue.enqueue(session, EnqueueSpec(type=clean_jobs, payload={}))
        job_id = job.id

    with session_scope() as session:
        queue.claim(session, "dead-worker", types=[clean_jobs])

    with session_scope() as session:
        assert queue.claim(session, "live-worker", types=[clean_jobs]) == []
        session.get(Job, job_id).claimed_at = datetime.now(UTC) - timedelta(
            seconds=queue.LEASE_SECONDS + 60
        )

    with session_scope() as session:
        reclaimed = queue.claim(session, "live-worker", types=[clean_jobs])
        assert [j.id for j in reclaimed] == [job_id]
        assert reclaimed[0].claimed_by == "live-worker"


def test_fan_out_tracks_children(clean_jobs):
    """One child per element, and the parent can tell when they are done."""
    with session_scope() as session:
        parent = queue.enqueue(session, EnqueueSpec(type=f"{clean_jobs}_parent", payload={}))
        children = queue.fan_out(
            session,
            parent,
            [EnqueueSpec(type=f"{clean_jobs}_child", payload={"i": i}) for i in range(4)],
        )
        parent_id = parent.id
        assert len(children) == 4
        assert all(c.parent_job_id == parent_id for c in children)

    with session_scope() as session:
        assert queue.children_pending(session, parent_id) == 4

    with session_scope() as session:
        for job in queue.claim(session, "w", types=[f"{clean_jobs}_child"], limit=4):
            queue.complete(session, job)

    with session_scope() as session:
        assert queue.children_pending(session, parent_id) == 0


def test_priority_orders_claims(clean_jobs):
    with session_scope() as session:
        queue.enqueue(session, EnqueueSpec(type=clean_jobs, payload={"p": "low"}, priority=200))
        queue.enqueue(session, EnqueueSpec(type=clean_jobs, payload={"p": "high"}, priority=10))

    with session_scope() as session:
        first = queue.claim(session, "w", types=[clean_jobs])[0]
        assert first.payload["p"] == "high"


async def test_worker_drains_queue(clean_jobs):
    """End to end: a registered handler runs and the row lands succeeded."""
    from vide.jobs.worker import _HANDLERS, Worker, WorkerConfig

    processed: list[int] = []

    async def double(payload: dict) -> dict:
        await asyncio.sleep(0.01)
        processed.append(payload["n"])
        return {"doubled": payload["n"] * 2}

    _HANDLERS[clean_jobs] = double
    try:
        with session_scope() as session:
            for i in range(5):
                queue.enqueue(session, EnqueueSpec(type=clean_jobs, payload={"n": i}))

        worker = Worker(WorkerConfig(concurrency=3, idle_sleep=0.05, types=[clean_jobs]))
        task = asyncio.create_task(worker.run())
        for _ in range(100):
            await asyncio.sleep(0.05)
            if len(processed) == 5:
                break
        worker.stop()
        await asyncio.wait_for(task, timeout=10)

        assert sorted(processed) == [0, 1, 2, 3, 4]
        with session_scope() as session:
            rows = list(session.scalars(queue.select(Job).where(Job.type == clean_jobs)))
            assert all(r.status == JobStatus.SUCCEEDED for r in rows)
            assert {r.result["doubled"] for r in rows} == {0, 2, 4, 6, 8}
    finally:
        _HANDLERS.pop(clean_jobs, None)


async def test_worker_records_handler_failure(clean_jobs):
    from vide.jobs.worker import _HANDLERS, Worker, WorkerConfig

    async def explode(payload: dict) -> dict:
        raise ValueError("handler blew up")

    _HANDLERS[clean_jobs] = explode
    try:
        with session_scope() as session:
            queue.enqueue(
                session, EnqueueSpec(type=clean_jobs, payload={}, max_attempts=1)
            )

        worker = Worker(WorkerConfig(concurrency=1, idle_sleep=0.05, types=[clean_jobs]))
        task = asyncio.create_task(worker.run())
        await asyncio.sleep(0.6)
        worker.stop()
        await asyncio.wait_for(task, timeout=10)

        with session_scope() as session:
            row = session.scalars(queue.select(Job).where(Job.type == clean_jobs)).one()
            assert row.status == JobStatus.FAILED
            assert "handler blew up" in row.error
    finally:
        _HANDLERS.pop(clean_jobs, None)

"""Jobs, comments and the audit log.

The ``jobs`` table is the orchestration substrate until Temporal arrives later.
Steps are idempotent and claimed with SKIP LOCKED, so the worker pool can scale
horizontally and a crashed worker's work is re-claimable.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from vide.db import Base
from vide.models.base import Timestamps, UUIDPk, enum_column
from vide.models.enums import JobStatus


class Job(Base, UUIDPk, Timestamps):
    __tablename__ = "jobs"
    __table_args__ = (
        Index("ix_jobs_claimable", "status", "run_after", "priority"),
    )

    project_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    type: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[JobStatus] = enum_column(
        JobStatus, nullable=False, default=JobStatus.PENDING, index=True
    )

    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    run_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    #: Fan-out: a parent job spawns one child per element (principle 2).
    parent_job_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), index=True
    )
    #: Dedupe handle — re-enqueuing the same unit of work is a no-op.
    idempotency_key: Mapped[str | None] = mapped_column(String(200), unique=True)

    claimed_by: Mapped[str | None] = mapped_column(String(120))
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    error: Mapped[str | None] = mapped_column(Text)


class Comment(Base, UUIDPk, Timestamps):
    __tablename__ = "comments"

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    target_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    target_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("comments.id", ondelete="CASCADE")
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    resolved: Mapped[bool] = mapped_column(nullable=False, default=False)


class AuditLog(Base, UUIDPk, Timestamps):
    __tablename__ = "audit_log"

    project_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    actor: Mapped[str | None] = mapped_column(String(120))
    action: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    target_type: Mapped[str | None] = mapped_column(String(40))
    target_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)
    detail: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

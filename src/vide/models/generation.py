"""The generation log: generations, reviews, gate decisions, prompt patches.

Together these make any generation replayable and give the benchmark its numbers.
The reference provider data had no reliable "chosen" field, which made finals
indistinguishable from attempts after the fact — ``GateDecision`` exists so that
cannot happen here (Appendix C).
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from vide.db import Base
from vide.models.base import Timestamps, UUIDPk, enum_column
from vide.models.enums import (
    ActorKind,
    FailureCategory,
    Gate,
    GateDecisionKind,
    GenerationStatus,
    ReviewVerdict,
)


class Generation(Base, UUIDPk, Timestamps):
    """One call to a model, with everything needed to reproduce it exactly."""

    __tablename__ = "generations"

    #: Nullable: not every model call belongs to a project. A call made outside
    #: one still belongs in the log, and losing it is worse than an unattributed
    #: row.
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    #: Which element this attempt belongs to — asset, shot keyframe, scene breakdown…
    target_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    target_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    provider: Mapped[str] = mapped_column(String(60), nullable=False)
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    params_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    prompt_text: Mapped[str | None] = mapped_column(Text)
    #: The prompt kept as named sections, so a fix can patch one and leave the rest
    #: byte-identical.
    prompt_sections_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    #: Named reference elements with their media, not bare URLs (Appendix C).
    reference_asset_versions: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    input_media_urls: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    #: Which skill and glossary/rule versions produced this.
    skill_versions_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    #: [{url, thumbnail_url, width, height, content_type}]. Dimensions are carried
    #: because the virtualised justified grid needs them before the image loads.
    outputs_json: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    #: Raw provider request/response, kept verbatim for replay.
    provider_job_id: Mapped[str | None] = mapped_column(String(200), index=True)
    raw_request_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    raw_response_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    cost_usd: Mapped[float | None] = mapped_column(Float)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    tokens_in: Mapped[int | None] = mapped_column(Integer)
    tokens_out: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[GenerationStatus] = enum_column(
        GenerationStatus, nullable=False, default=GenerationStatus.QUEUED
    )
    error: Mapped[str | None] = mapped_column(Text)

    created_by_kind: Mapped[ActorKind] = enum_column(
        ActorKind, nullable=False, default=ActorKind.AGENT
    )
    created_by_agent: Mapped[str | None] = mapped_column(String(80))
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))

    reviews: Mapped[list["Review"]] = relationship(
        back_populates="generation", cascade="all, delete-orphan"
    )


class Review(Base, UUIDPk, Timestamps):
    """An agent or human verdict on one generation.

    Rejections are never deleted — they stay visible behind the Agent-rejected
    filter so a human can override.
    """

    __tablename__ = "reviews"

    generation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("generations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    reviewer_kind: Mapped[ActorKind] = enum_column(ActorKind, nullable=False)
    reviewer_agent: Mapped[str | None] = mapped_column(String(80))
    reviewer_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))

    verdict: Mapped[ReviewVerdict] = enum_column(ReviewVerdict, nullable=False)
    category: Mapped[FailureCategory | None] = enum_column(FailureCategory)
    #: Plain language, because a reviewer reads this, not a machine.
    reason: Mapped[str | None] = mapped_column(Text)
    proposed_fix: Mapped[str | None] = mapped_column(Text)
    #: face_similarity, colour_histogram_delta, ssim, person_count, ocr_text…
    metrics_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    #: Named rules from the rules library that this output violated.
    rule_violations: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    generation: Mapped[Generation] = relationship(back_populates="reviews")


class GateDecision(Base, UUIDPk, Timestamps):
    """A human decision at a gate. The explicit record of what was chosen."""

    __tablename__ = "gate_decisions"

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    gate: Mapped[Gate] = enum_column(Gate, nullable=False, index=True)
    target_type: Mapped[str] = mapped_column(String(40), nullable=False)
    target_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    chosen_generation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("generations.id", ondelete="SET NULL")
    )
    decision: Mapped[GateDecisionKind] = enum_column(GateDecisionKind, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    decided_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    decided_at: Mapped[datetime | None] = mapped_column()


class PromptPatch(Base, UUIDPk, Timestamps):
    """What changed between two attempts, and why (principle 5).

    One section per patch. A fully rewritten prompt loses the parts that worked.
    """

    __tablename__ = "prompt_patches"

    generation_from_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("generations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    generation_to_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("generations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    section: Mapped[str] = mapped_column(String(80), nullable=False)
    diff: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(Text)
    category: Mapped[FailureCategory | None] = enum_column(FailureCategory)

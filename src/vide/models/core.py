"""Organisations, users, projects, and the ingested script hierarchy."""

import uuid

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from vide.db import Base
from vide.models.base import Timestamps, UUIDPk, enum_column
from vide.models.enums import (
    ElementStatus,
    IntExt,
    ProjectFormat,
    ProjectStatus,
    Role,
    SourceKind,
    TimeOfDay,
)


class Organization(Base, UUIDPk, Timestamps):
    __tablename__ = "organizations"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)


class User(Base, UUIDPk, Timestamps):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    password_hash: Mapped[str | None] = mapped_column(Text)
    avatar_url: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class Membership(Base, UUIDPk, Timestamps):
    """Org-level role. Project-level access lives in ProjectMember."""

    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("user_id", "organization_id"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[Role] = enum_column(Role, nullable=False)


class Project(Base, UUIDPk, Timestamps):
    __tablename__ = "projects"
    __table_args__ = (UniqueConstraint("organization_id", "code"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    #: Short project code used in asset tags, e.g. "CB" in @char_CB_Kel_v9.
    code: Mapped[str] = mapped_column(String(16), nullable=False)
    client_name: Mapped[str | None] = mapped_column(String(200))
    cover_image_url: Mapped[str | None] = mapped_column(Text)

    format: Mapped[ProjectFormat] = enum_column(
        ProjectFormat, nullable=False, default=ProjectFormat.MICRO_DRAMA
    )
    aspect_delivery: Mapped[str] = mapped_column(String(16), nullable=False, default="9:16")
    episodes_target: Mapped[int | None] = mapped_column(Integer)
    #: Per-episode runtime target in seconds.
    runtime_target_s: Mapped[int | None] = mapped_column(Integer)
    #: Period constraint, e.g. "2011". Drives the era lock.
    era: Mapped[str | None] = mapped_column(String(80))
    model_tier: Mapped[str] = mapped_column(String(40), nullable=False, default="standard")

    #: #6 and #7 — configurable per project.
    candidates_per_asset: Mapped[int] = mapped_column(Integer, nullable=False, default=4)
    attempt_budget: Mapped[int] = mapped_column(Integer, nullable=False, default=4)
    #: #8 / #9 — who approves what.
    keyframe_gate_audience: Mapped[str] = mapped_column(String(16), nullable=False, default="team")
    client_approves_story: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    budget_ceiling_usd: Mapped[float | None] = mapped_column()
    status: Mapped[ProjectStatus] = enum_column(
        ProjectStatus, nullable=False, default=ProjectStatus.DRAFT
    )
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))

    episodes: Mapped[list["Episode"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )


class ProjectMember(Base, UUIDPk, Timestamps):
    """Per-project access. This is what scopes a client to their own projects."""

    __tablename__ = "project_members"
    __table_args__ = (UniqueConstraint("project_id", "user_id"),)

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[Role] = enum_column(Role, nullable=False)


class SourceDocument(Base, UUIDPk, Timestamps):
    """A file the client provided, plus its normalised text."""

    __tablename__ = "source_documents"

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[SourceKind] = enum_column(SourceKind, nullable=False)
    filename: Mapped[str | None] = mapped_column(Text)
    file_url: Mapped[str | None] = mapped_column(Text)
    content_type: Mapped[str | None] = mapped_column(String(120))
    #: Normalised plain text. Source spans index into this.
    text: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    uploaded_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    meta_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)


class Episode(Base, UUIDPk, Timestamps):
    __tablename__ = "episodes"
    __table_args__ = (UniqueConstraint("project_id", "number"),)

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    #: Some scripts split an episode, e.g. EP03.1 / EP03.2. Keeps the label the client uses.
    label: Mapped[str | None] = mapped_column(String(40))
    logline: Mapped[str | None] = mapped_column(Text)
    runtime_target_s: Mapped[int | None] = mapped_column(Integer)

    project: Mapped[Project] = relationship(back_populates="episodes")
    scenes: Mapped[list["Scene"]] = relationship(
        back_populates="episode", cascade="all, delete-orphan"
    )


class Scene(Base, UUIDPk, Timestamps):
    __tablename__ = "scenes"

    episode_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("episodes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    #: Scripts use "2A", "2/A" and similar. Preserved verbatim alongside the integer.
    number_label: Mapped[str | None] = mapped_column(String(24))
    #: Scenes joined by INT CUT / INTERCUT share a group id and play simultaneously.
    intercut_group: Mapped[str | None] = mapped_column(String(40), index=True)

    int_ext: Mapped[IntExt | None] = enum_column(IntExt)
    time_of_day: Mapped[TimeOfDay] = enum_column(
        TimeOfDay, nullable=False, default=TimeOfDay.UNSPECIFIED
    )
    #: Assigned by story-day inference, confirmed by a human at G1.
    story_day: Mapped[str | None] = mapped_column(String(16), index=True)
    story_day_hint: Mapped[str | None] = mapped_column(Text)
    story_day_confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    #: Raw strings as the script wrote them, before entity resolution.
    location_raw: Mapped[str | None] = mapped_column(Text)
    sub_area_raw: Mapped[str | None] = mapped_column(Text)
    location_sub_area_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("location_sub_areas.id", ondelete="SET NULL"), index=True
    )

    heading_raw: Mapped[str | None] = mapped_column(Text)
    action_text: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    emotional_beat: Mapped[str | None] = mapped_column(Text)
    transition_out: Mapped[str | None] = mapped_column(String(40))

    #: {document_id, start_line, end_line, start_char, end_char} — click-through to source.
    source_span: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    #: The full breakdown record, kept verbatim as the agent returned it.
    breakdown_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    gaps: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    status: Mapped[ElementStatus] = enum_column(
        ElementStatus, nullable=False, default=ElementStatus.PLANNED
    )

    episode: Mapped[Episode] = relationship(back_populates="scenes")
    dialogue: Mapped[list["DialogueLine"]] = relationship(
        back_populates="scene", cascade="all, delete-orphan"
    )
    mentions: Mapped[list["SceneMention"]] = relationship(
        back_populates="scene", cascade="all, delete-orphan"
    )


class SceneMention(Base, UUIDPk, Timestamps):
    """One raw reference to an entity inside one scene, before resolution."""

    __tablename__ = "scene_mentions"

    scene_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("scenes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    #: Where in the scene it came from — heading, action line or dialogue cue.
    origin: Mapped[str | None] = mapped_column(String(32))
    speaks: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    #: Untyped on purpose: points at characters / locations / props by entity_type.
    resolved_entity_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)
    confidence: Mapped[float | None] = mapped_column()

    scene: Mapped[Scene] = relationship(back_populates="mentions")


class DialogueLine(Base, UUIDPk, Timestamps):
    __tablename__ = "dialogue_lines"

    scene_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("scenes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    order: Mapped[int] = mapped_column(Integer, nullable=False)
    speaker_raw: Mapped[str] = mapped_column(Text, nullable=False)
    speaker_character_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("characters.id", ondelete="SET NULL"), index=True
    )
    line: Mapped[str] = mapped_column(Text, nullable=False)
    #: The house format carries two cues per line: a physical action and an emotion,
    #: e.g. NAME (Turns sharply, Guarded). They drive different things —
    #: the action is blocking, the emotion is performance — so they are kept apart.
    delivery_action: Mapped[str | None] = mapped_column(Text)
    delivery_emotion: Mapped[str | None] = mapped_column(Text)
    delivery_raw: Mapped[str | None] = mapped_column(Text)
    source_span: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    scene: Mapped[Scene] = relationship(back_populates="dialogue")


class SceneCharacter(Base, UUIDPk, Timestamps):
    """Resolved presence: which character is in which scene, and do they speak."""

    __tablename__ = "scene_characters"
    __table_args__ = (UniqueConstraint("scene_id", "character_id"),)

    scene_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("scenes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    character_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("characters.id", ondelete="CASCADE"), nullable=False, index=True
    )
    speaks: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    line_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

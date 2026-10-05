"""Canonical entities: characters, locations, sub-areas, props, colour worlds."""

import uuid

from pgvector.sqlalchemy import Vector
from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, UniqueConstraint, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from vide.db import Base
from vide.models.base import Timestamps, UUIDPk, enum_column
from vide.models.enums import CharacterTier, ElementStatus, IntExt, PropKind

#: Dimension of the embedding used for entity-resolution clustering.
EMBEDDING_DIM = 1536


class World(Base, UUIDPk, Timestamps):
    """A colour/look world. Estate, club, police — each with one register."""

    __tablename__ = "worlds"
    __table_args__ = (UniqueConstraint("project_id", "name"),)

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    #: Base field / accents / counter-note, with percentages.
    palette_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    #: Reused verbatim in every prompt for this world.
    style_prefix: Mapped[str | None] = mapped_column(Text)
    era_rules: Mapped[str | None] = mapped_column(Text)
    #: What separates this world from a similar one — finish and age, not hue.
    finish_note: Mapped[str | None] = mapped_column(Text)


class Character(Base, UUIDPk, Timestamps):
    __tablename__ = "characters"
    __table_args__ = (UniqueConstraint("project_id", "name"),)

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    tier: Mapped[CharacterTier | None] = enum_column(CharacterTier)
    #: Computed at resolution time; drives the tier and the build order.
    scene_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    line_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    speaks: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    #: spec: age/build, face & body lock, marks, hair, wardrobe, props, brief, arc.
    spec_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    #: — one paragraph, 150–220 words, observable behaviour only.
    acting_profile: Mapped[str | None] = mapped_column(Text)
    #: — pasted verbatim wherever the character speaks. Never paraphrased.
    voice_block: Mapped[str | None] = mapped_column(Text)
    #: Every inferred or missing detail, surfaced rather than silently invented.
    gaps: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    status: Mapped[ElementStatus] = enum_column(
        ElementStatus, nullable=False, default=ElementStatus.PLANNED
    )


class Location(Base, UUIDPk, Timestamps):
    """A main location. Sub-areas hang off it."""

    __tablename__ = "locations"
    __table_args__ = (UniqueConstraint("project_id", "name"),)

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)

    sub_areas: Mapped[list["LocationSubArea"]] = relationship(
        back_populates="location", cascade="all, delete-orphan"
    )


class LocationSubArea(Base, UUIDPk, Timestamps):
    __tablename__ = "location_sub_areas"
    __table_args__ = (UniqueConstraint("location_id", "name"),)

    location_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("locations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    int_ext: Mapped[IntExt | None] = enum_column(IntExt)
    #: Distinct times of day this sub-area is used at — each one is a separate asset.
    times_of_day: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    scene_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    #: spec: geography brief, anchor objects, light source, hero props, era, risks.
    spec_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    world_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("worlds.id", ondelete="SET NULL"), index=True
    )
    #: e.g. "heaviest-reuse set, build first".
    continuity_risk: Mapped[str | None] = mapped_column(Text)
    gaps: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    status: Mapped[ElementStatus] = enum_column(
        ElementStatus, nullable=False, default=ElementStatus.PLANNED
    )

    location: Mapped[Location] = relationship(back_populates="sub_areas")


class Prop(Base, UUIDPk, Timestamps):
    __tablename__ = "props"
    __table_args__ = (UniqueConstraint("project_id", "name"),)

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    kind: Mapped[PropKind] = enum_column(PropKind, nullable=False, default=PropKind.PROP)
    #: : materials, wear, scale, states needed, exact copy for any text on it.
    spec_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    scene_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    gaps: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    status: Mapped[ElementStatus] = enum_column(
        ElementStatus, nullable=False, default=ElementStatus.PLANNED
    )


class EntitySurface(Base, UUIDPk, Timestamps):
    """A distinct surface form the script uses for an entity.

    "Riya", "her apartment", "the girl in the red kurta" are surfaces. Embedding one
    row per distinct string rather than per mention keeps clustering cheap on a
    40-episode script where a lead is named hundreds of times.
    """

    __tablename__ = "entity_surfaces"
    __table_args__ = (UniqueConstraint("project_id", "entity_type", "surface_text"),)

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    surface_text: Mapped[str] = mapped_column(Text, nullable=False)
    mention_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))
    #: What the clustering proposed vs what a human confirmed at G1.
    proposed_entity_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)
    resolved_entity_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)
    confidence: Mapped[float | None] = mapped_column()


class MergeQuestion(Base, UUIDPk, Timestamps):
    """An explicit G1 question the system will not answer on its own.

    "server room (Ep 1) vs server floor (Ep 14): same set?" — a design call, not an
    extraction problem. Surfaced rather than guessed.
    """

    __tablename__ = "merge_questions"

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    #: The candidate entities or surfaces in play, with the evidence for each.
    candidates_json: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    recommendation: Mapped[str | None] = mapped_column(Text)
    resolved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    decision: Mapped[str | None] = mapped_column(Text)
    decided_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))

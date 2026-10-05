"""Assets, versions, the continuity matrix, shots.

The design order is not a table. It materialises as ``Asset`` rows with
status ``planned`` — one per thing that has to be built — so the build list and the
production tracker are the same object rather than two that can disagree.
"""

import uuid

from sqlalchemy import (
    Boolean,
    Float,
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
from vide.models.enums import AssetKind, Complexity, ElementStatus, PlateType, TimeOfDay


class Asset(Base, UUIDPk, Timestamps):
    """A descriptor + reference image pair for one master or one state.

    The descriptor goes into every prompt that uses this asset, verbatim (principle 4).
    """

    __tablename__ = "assets"
    __table_args__ = (UniqueConstraint("project_id", "tag"),)

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    #: Untyped by design — resolves against characters / location_sub_areas / props.
    entity_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)

    kind: Mapped[AssetKind] = enum_column(AssetKind, nullable=False)
    plate_type: Mapped[PlateType | None] = enum_column(PlateType)
    #: e.g. "Wedding day, Ep.3", "wet", "night".
    variant_label: Mapped[str | None] = mapped_column(String(200))
    time_of_day: Mapped[TimeOfDay | None] = enum_column(TimeOfDay)
    #: Story-days this state is worn on. Drives costume continuity.
    story_days: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    scenes_mark: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    #: A variant is an edit of its parent, never a fresh generation.
    parent_asset_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("assets.id", ondelete="SET NULL"), index=True
    )
    #: @{type}_{PROJ}_{name}[_s{scenes}]_v{n} — stable across creation, prompts and UI.
    tag: Mapped[str] = mapped_column(String(200), nullable=False)
    descriptor_text: Mapped[str | None] = mapped_column(Text)
    #: Aspect this asset is generated at. Geography masters are 16:9 regardless of delivery.
    aspect: Mapped[str | None] = mapped_column(String(16))

    #: Why this asset exists in the build list, for the design-order view.
    reason: Mapped[str | None] = mapped_column(Text)
    #: e.g. a branded shirt removed on camera in Ep 5 — every later shot that day differs.
    plot_critical_note: Mapped[str | None] = mapped_column(Text)
    gaps: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    status: Mapped[ElementStatus] = enum_column(
        ElementStatus, nullable=False, default=ElementStatus.PLANNED
    )
    #: Set when an upstream master changes after approval. Flagged, not auto-regenerated.
    stale_reason: Mapped[str | None] = mapped_column(Text)
    approved_version_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)

    versions: Mapped[list["AssetVersion"]] = relationship(
        back_populates="asset",
        cascade="all, delete-orphan",
        foreign_keys="AssetVersion.asset_id",
    )


class AssetVersion(Base, UUIDPk, Timestamps):
    """Immutable. A new variant never overwrites the old one (principle 10)."""

    __tablename__ = "asset_versions"
    __table_args__ = (UniqueConstraint("asset_id", "version"),)

    asset_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("assets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    #: [{url, thumbnail_url, width, height}] — dimensions are needed by the
    #: virtualised justified grid before the image loads.
    images_json: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    descriptor_text: Mapped[str | None] = mapped_column(Text)
    chosen_generation_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)
    approved_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    approved_at: Mapped[str | None] = mapped_column(Text)

    asset: Mapped[Asset] = relationship(back_populates="versions", foreign_keys=[asset_id])


class ContinuityEntry(Base, UUIDPk, Timestamps):
    """One cell of the continuity matrix: story-day × character → state."""

    __tablename__ = "continuity"
    __table_args__ = (UniqueConstraint("project_id", "story_day", "character_id"),)

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    story_day: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    character_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("characters.id", ondelete="CASCADE"), nullable=False, index=True
    )
    costume_asset_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("assets.id", ondelete="SET NULL")
    )
    costume_label: Mapped[str | None] = mapped_column(String(200))
    hair: Mapped[str | None] = mapped_column(Text)
    state_notes: Mapped[str | None] = mapped_column(Text)
    #: Episodes on this story-day where the character appears.
    episodes: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)


class Shot(Base, UUIDPk, Timestamps):
    """One generated clip. Scene 50 has shots 50A, 50B…."""

    __tablename__ = "shots"
    __table_args__ = (UniqueConstraint("scene_id", "letter"),)

    scene_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("scenes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    letter: Mapped[str] = mapped_column(String(8), nullable=False)
    order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    duration_s: Mapped[float | None] = mapped_column(Float)
    complexity: Mapped[Complexity | None] = enum_column(Complexity)
    #: The four groups: material, direction, camera, edit (Appendix B.1).
    shot_card_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    #: Written once per scene, pasted unchanged into every shot of that scene (Appendix B.3).
    spatial_map: Mapped[str | None] = mapped_column(Text)
    #: The ~1s master wide that opens a scene and fixes the blocking.
    is_master_wide: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[ElementStatus] = enum_column(
        ElementStatus, nullable=False, default=ElementStatus.PLANNED
    )


class ShotAssetRef(Base, UUIDPk, Timestamps):
    """Exactly which asset versions a shot consumes.

    Version-pinned, so changing a master marks dependent shots stale rather than
    silently altering what was approved.
    """

    __tablename__ = "shot_asset_refs"
    __table_args__ = (UniqueConstraint("shot_id", "asset_version_id"),)

    shot_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("shots.id", ondelete="CASCADE"), nullable=False, index=True
    )
    asset_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("asset_versions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[str | None] = mapped_column(String(40))

"""Skill registry, glossary and rules library.

All three are data, not code. Swapping a skill or a rule is a configuration change:
agents ask for a name, the registry hands back the active version.
"""

import uuid

from pgvector.sqlalchemy import Vector
from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from vide.db import Base
from vide.models.base import Timestamps, UUIDPk, enum_column
from vide.models.entity import EMBEDDING_DIM
from vide.models.enums import SkillSource


class Skill(Base, UUIDPk, Timestamps):
    """A versioned instruction document an agent loads into context."""

    __tablename__ = "skills"
    __table_args__ = (UniqueConstraint("name", "version"),)

    name: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str | None] = mapped_column(String(40))
    source: Mapped[SkillSource] = enum_column(
        SkillSource, nullable=False, default=SkillSource.IN_HOUSE
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_url: Mapped[str | None] = mapped_column(Text)
    #: sha256 of content, so a file edit is detectable on sync.
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    #: Exactly one version per name is active; the registry returns that one.
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)


class ProjectSkillPin(Base, UUIDPk, Timestamps):
    """Pin a project to a specific skill version, for A/B runs."""

    __tablename__ = "project_skill_pins"
    __table_args__ = (UniqueConstraint("project_id", "skill_name"),)

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    skill_name: Mapped[str] = mapped_column(String(80), nullable=False)
    skill_version: Mapped[int] = mapped_column(Integer, nullable=False)


class GlossaryEntry(Base, UUIDPk, Timestamps):
    """Plain intent → technical wording.

    The shot planner sees a compact index, picks the relevant entries and composes
    their ``technical_text`` into the prompt.
    """

    __tablename__ = "glossary_entries"
    __table_args__ = (UniqueConstraint("slug", "version"),)

    slug: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    category: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    #: Several phrasings of how a non-expert would describe this.
    intent_plain: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    technical_text: Mapped[str] = mapped_column(Text, nullable=False)
    when_to_use: Mapped[str | None] = mapped_column(Text)
    avoid_when: Mapped[str | None] = mapped_column(Text)
    pairs_with: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    #: Per-model phrasing notes, e.g. "needs ≥4 telephoto outcome phrases".
    model_notes_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    #: What goes wrong without this entry. Grows from the generation log.
    failure_note: Mapped[str | None] = mapped_column(Text)
    examples: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    source: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    #: Embedded over intent_plain, so plain-language lookup works.
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))


class Rule(Base, UUIDPk, Timestamps):
    """A named law.

    A rule becomes a law when it has a name, the wording that enforces it, the visible
    proof in frame, and a sentence stating what counts as a failed take.
    """

    __tablename__ = "rules"
    __table_args__ = (UniqueConstraint("slug", "version"),)

    slug: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    category: Mapped[str | None] = mapped_column(String(40))
    #: The wording inserted into the prompt to enforce it.
    prompt_text: Mapped[str] = mapped_column(Text, nullable=False)
    #: What the reviewer should be able to see in frame if it held.
    visible_proof: Mapped[str | None] = mapped_column(Text)
    #: "… = failed take."
    failure_sentence: Mapped[str | None] = mapped_column(Text)
    applies_to: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    #: Stage 1 rules apply to images; some, like first-frame occupancy, are Stage 2.
    stage: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

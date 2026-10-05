"""SQLAlchemy models for the data model.

Importing this package registers every table on ``Base.metadata``, which is what
Alembic autogenerate reads.
"""

from vide.models.asset import (
    Asset,
    AssetVersion,
    ContinuityEntry,
    Shot,
    ShotAssetRef,
)
from vide.models.core import (
    DialogueLine,
    Episode,
    Membership,
    Organization,
    Project,
    ProjectMember,
    Scene,
    SceneCharacter,
    SceneMention,
    SourceDocument,
    User,
)
from vide.models.entity import (
    Character,
    EntitySurface,
    Location,
    LocationSubArea,
    MergeQuestion,
    Prop,
    World,
)
from vide.models.generation import (
    GateDecision,
    Generation,
    PromptPatch,
    Review,
)
from vide.models.ops import AuditLog, Comment, Job
from vide.models.registry import (
    GlossaryEntry,
    ProjectSkillPin,
    Rule,
    Skill,
)

__all__ = [
    "Asset",
    "AssetVersion",
    "AuditLog",
    "Character",
    "Comment",
    "ContinuityEntry",
    "DialogueLine",
    "EntitySurface",
    "Episode",
    "GateDecision",
    "Generation",
    "GlossaryEntry",
    "Job",
    "Location",
    "LocationSubArea",
    "Membership",
    "MergeQuestion",
    "Organization",
    "Project",
    "ProjectMember",
    "ProjectSkillPin",
    "PromptPatch",
    "Prop",
    "Review",
    "Rule",
    "Scene",
    "SceneCharacter",
    "SceneMention",
    "Shot",
    "ShotAssetRef",
    "Skill",
    "SourceDocument",
    "User",
    "World",
]

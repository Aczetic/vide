"""Controlled vocabularies used across the schema.

Stored as VARCHAR + CHECK constraint rather than native Postgres enums, because
native enums are painful to alter and several of these will grow.
"""

from enum import StrEnum


class Role(StrEnum):
    """Client access is per-project and enforced server-side."""

    ADMIN = "admin"
    PRODUCER = "producer"
    REVIEWER = "reviewer"
    CLIENT = "client"


class ProjectFormat(StrEnum):
    MICRO_DRAMA = "micro_drama"
    FEATURE = "feature"
    SHORT = "short"
    OTHER = "other"


class ProjectStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    PAUSED = "paused"
    DELIVERED = "delivered"
    ARCHIVED = "archived"


class SourceKind(StrEnum):
    """entry points. The pipeline starts from whichever is provided."""

    PLOT = "plot"
    BIBLE = "bible"
    SCREENPLAY = "screenplay"
    BREAKDOWN = "breakdown"
    REFERENCE = "reference"


class EntityType(StrEnum):
    CHARACTER = "character"
    LOCATION = "location"
    LOCATION_SUB_AREA = "location_sub_area"
    PROP = "prop"


class ElementStatus(StrEnum):
    """Every element is its own little state machine."""

    PLANNED = "planned"
    PROMPTING = "prompting"
    GENERATING = "generating"
    AGENT_REVIEW = "agent_review"
    AWAITING_GATE = "awaiting_gate"
    APPROVED = "approved"
    REJECTED = "rejected"
    ESCALATED = "escalated"
    STALE = "stale"


class CharacterTier(StrEnum):
    """, assigned from scene counts and line counts."""

    LEAD = "lead"
    SUPPORTING = "supporting"
    ENSEMBLE = "ensemble"
    FEATURED = "featured"
    BIT = "bit"


class PropKind(StrEnum):
    PROP = "prop"
    VEHICLE = "vehicle"
    VEHICLE_INTERIOR = "vehicle_interior"
    SCREEN_INSERT = "screen_insert"


class AssetKind(StrEnum):
    MASTER = "master"
    VARIANT = "variant"
    VIEW = "view"
    PLATE = "plate"
    SHEET = "sheet"


class PlateType(StrEnum):
    """two-tier plates. A geography master is never a first frame."""

    GEOGRAPHY_MASTER = "geography_master"
    COVERAGE_PLATE = "coverage_plate"
    REVERSE = "reverse"
    BACKGROUND = "background"
    VIEW = "view"


class IntExt(StrEnum):
    INT = "INT"
    EXT = "EXT"
    INT_EXT = "INT/EXT"


class TimeOfDay(StrEnum):
    DAY = "DAY"
    NIGHT = "NIGHT"
    MORNING = "MORNING"
    EVENING = "EVENING"
    DAWN = "DAWN"
    DUSK = "DUSK"
    CONTINUOUS = "CONTINUOUS"
    UNSPECIFIED = "UNSPECIFIED"


class Complexity(StrEnum):
    """Complex shots are split before generation, not after they fail."""

    SIMPLE = "simple"
    MEDIUM = "medium"
    COMPLEX = "complex"


class GenerationStatus(StrEnum):
    QUEUED = "queued"
    SUBMITTED = "submitted"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ActorKind(StrEnum):
    AGENT = "agent"
    USER = "user"


class ReviewVerdict(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    OVERRIDE_PASS = "override_pass"
    OVERRIDE_FAIL = "override_fail"


class FailureCategory(StrEnum):
    """The category decides what gets changed, which is the whole point."""

    PROMPT_AMBIGUITY = "prompt_ambiguity"
    PROMPT_CONTRADICTION = "prompt_contradiction"
    MODEL_LIMITATION = "model_limitation"
    ASSET_WEAKNESS = "asset_weakness"
    REFERENCE_ERROR = "reference_error"
    RULE_VIOLATION = "rule_violation"


class Gate(StrEnum):
    """Gates are the only blocking points in the system."""

    G0 = "G0"  # story / screenplay
    G1 = "G1"  # entity merges + story-days
    G2 = "G2"  # character / location / prop images — client picks
    G3 = "G3"  # shot keyframes
    G4 = "G4"  # final video per scene (Stage 2)


class GateDecisionKind(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"
    CHANGES_REQUESTED = "changes_requested"


class SkillSource(StrEnum):
    EXTERNAL_REFERENCE = "external-reference"
    IN_HOUSE = "in-house"


class JobStatus(StrEnum):
    PENDING = "pending"
    CLAIMED = "claimed"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"

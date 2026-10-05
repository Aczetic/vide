"""Response shapes for the API.

Deliberately UI-shaped rather than table-shaped: the Watchlist tile needs
progress counters, and the Story tab needs a scene with its breakdown attached.
Making the client assemble those from normalised rows would mean a dozen
requests to paint one screen.
"""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel


class ProjectTile(BaseModel):
    """— what a Watchlist tile shows."""

    id: uuid.UUID
    name: str
    code: str
    client_name: str | None
    format: str
    status: str
    episodes: int
    scenes: int
    cover_image_url: str | None
    #: Pipeline progress: approved out of planned, per element type.
    progress: dict[str, dict[str, int]]
    #: The badge count — how much is waiting on a human.
    awaiting_decision: int
    last_activity: str | None


class ProjectDetail(BaseModel):
    id: uuid.UUID
    name: str
    code: str
    client_name: str | None
    format: str
    aspect_delivery: str
    episodes_target: int | None
    runtime_target_s: int | None
    era: str | None
    model_tier: str
    candidates_per_asset: int
    attempt_budget: int
    status: str
    sources: list[dict[str, Any]]
    counts: dict[str, int]


class SceneSummary(BaseModel):
    id: uuid.UUID
    number: int
    number_label: str
    heading: str | None
    int_ext: str | None
    time_of_day: str
    story_day: str | None
    location_raw: str | None
    sub_area_raw: str | None
    intercut_group: str | None
    character_count: int
    dialogue_count: int
    gap_count: int


class EpisodeSummary(BaseModel):
    id: uuid.UUID
    number: int
    label: str | None
    logline: str | None
    scenes: list[SceneSummary]


class DialogueOut(BaseModel):
    order: int
    speaker: str
    line: str
    #: Split apart because they drive different things — blocking vs performance.
    delivery_action: str | None
    delivery_emotion: str | None


class SceneDetail(BaseModel):
    id: uuid.UUID
    episode_number: int
    number_label: str
    heading: str | None
    int_ext: str | None
    time_of_day: str
    story_day: str | None
    story_day_hint: str | None
    location_raw: str | None
    sub_area_raw: str | None
    intercut_group: str | None
    action_text: str | None
    summary: str | None
    emotional_beat: str | None
    transition_out: str | None
    #: Click-through to the original text.
    source_span: dict[str, Any]
    characters: list[dict[str, Any]]
    dialogue: list[DialogueOut]
    breakdown: dict[str, Any]
    gaps: list[str]


class EntityOut(BaseModel):
    id: uuid.UUID
    name: str
    entity_type: str
    tier: str | None
    scene_count: int
    line_count: int
    surfaces: list[str]
    gaps: list[str]


class QuestionOut(BaseModel):
    """A G1 decision. The system refused to guess; a human answers once."""

    id: uuid.UUID
    entity_type: str
    question: str
    candidates: list[str]
    recommendation: str | None
    resolved: bool
    decision: str | None


class BuildItemOut(BaseModel):
    id: uuid.UUID
    entity_type: str
    entity_name: str
    variant_label: str | None
    plate_type: str | None
    reason: str | None
    tag: str
    status: str
    scene_count: int
    gaps: list[str]


class GapOut(BaseModel):
    scene_id: uuid.UUID | None
    scene_label: str
    episode: int
    text: str
    kind: str


class CandidateOut(BaseModel):
    generation_id: uuid.UUID
    url: str
    width: int | None
    height: int | None
    status: str
    #: Agent verdict, once the review agent has run.
    verdict: str | None
    reason: str | None
    category: str | None
    chosen: bool


class AssetOut(BaseModel):
    """One element and its candidate images — what a grid group shows."""

    id: uuid.UUID
    entity_type: str
    entity_name: str
    variant_label: str | None
    kind: str
    tag: str
    status: str
    gaps: list[str]
    scene_count: int
    candidates: list[CandidateOut]
    #: Spec text shown in the overlay pane.
    spec: dict[str, Any]
    prompt: str | None
    prompt_sections: dict[str, Any]
    model: str | None


class GateDecisionIn(BaseModel):
    """A human's decision at a gate."""

    gate: str = "G2"
    target_type: str = "asset"
    target_id: uuid.UUID
    chosen_generation_id: uuid.UUID | None = None
    decision: str = "approved"
    notes: str | None = None


class GateDecisionOut(BaseModel):
    id: uuid.UUID
    gate: str
    target_id: uuid.UUID
    chosen_generation_id: uuid.UUID | None
    decision: str
    new_status: str


class ShotOut(BaseModel):
    id: uuid.UUID
    letter: str
    order: int
    duration_s: float | None
    complexity: str | None
    is_master_wide: bool
    card: dict[str, Any]
    status: str
    keyframes: list[CandidateOut]


class SceneShotsOut(BaseModel):
    scene_id: uuid.UUID
    episode: int
    scene_label: str
    heading: str | None
    spatial_map: str | None
    shots: list[ShotOut]


class QueueItem(BaseModel):
    """One thing waiting on a human, across all projects."""

    id: uuid.UUID
    project_id: uuid.UUID
    project_name: str
    kind: str
    gate: str
    title: str
    subtitle: str | None
    candidates: int
    thumbnail: str | None
    waiting_since: str | None


class ReportsOut(BaseModel):
    """'s numbers. Everything here is computed from the generation log."""

    total_generations: int
    by_status: dict[str, int]
    by_agent: dict[str, dict[str, float]]
    #: Of elements that reached a verdict, how many passed on attempt 1.
    first_pass_rate: float | None
    attempts_to_approval: float | None
    cost_total: float
    cost_by_target_type: dict[str, float]
    cost_per_approved_element: float | None
    top_failure_categories: list[dict[str, Any]]
    tokens_in: int
    tokens_out: int
    #: Image spend is not reported by the provider, so it is absent rather than
    #: guessed. Stated explicitly so the total is not mistaken for complete.
    unpriced_generations: int


class SkillOut(BaseModel):
    id: uuid.UUID
    name: str
    version: int
    kind: str | None
    source: str
    description: str | None
    active: bool
    chars: int
    content_hash: str

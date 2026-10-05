"""Story and screenplay generation.

For the entry points where the client hands over a logline or a bible instead of
a finished script. The pipeline generates only what is missing and then rejoins
the normal path at breakdown.

Episodes generate in parallel once the outline is fixed — but not before, because
every episode has to agree about who people are and what has already happened.
The outline is the shared state; generating episodes without one produces
forty self-consistent, mutually contradictory half-hours.

The reviewer runs on the result and checks the things that are expensive to find
later: timeline contradictions, arcs that stall, a cast that grew, episodes that
overrun, and micro-drama episodes that end without a hook.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import structlog

from vide.providers import LLMRequest, LogContext, TaskType, get_registry

log = structlog.get_logger(__name__)


def _system(name: str) -> str:
    """Load an agent's system prompt from the skill registry.

    System prompts are skill documents rather than literals: they are versioned,
    pinnable per project and comparable between versions, none of which is
    possible for a string baked into a module. The documents are not included in
    this repository — see skills/README.md.
    """
    from vide.db import session_scope as _scope
    from vide.registry import resolve as _resolve

    with _scope() as session:
        return _resolve(session, name).content

BIBLE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "logline": {"type": "string"},
        "premise": {"type": "string"},
        "world": {"type": "string", "description": "Setting, period, social texture."},
        "tone": {"type": "string"},
        "characters": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "role": {"type": "string"},
                    "physical": {
                        "type": "string",
                        "description": (
                            "At least one concrete physical description. A "
                            "character never described cannot be generated."
                        ),
                    },
                    "wants": {"type": "string"},
                    "obstacle": {"type": "string"},
                    "arc": {"type": "string"},
                },
                "required": ["name", "role", "physical", "wants", "obstacle", "arc"],
                "additionalProperties": False,
            },
        },
        "locations": {
            "type": "array",
            "description": "Every named set. Reuse is deliberate — each one is a build.",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "episodes": {"type": "string"},
                },
                "required": ["name", "description", "episodes"],
                "additionalProperties": False,
            },
        },
        "episodes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "number": {"type": "integer"},
                    "logline": {"type": "string"},
                    "beats": {"type": "string", "description": "What happens, in order."},
                    "hook": {
                        "type": "string",
                        "description": "The turn or question it ends on.",
                    },
                    "locations": {"type": "array", "items": {"type": "string"}},
                    "characters": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["number", "logline", "beats", "hook", "locations", "characters"],
                "additionalProperties": False,
            },
        },
    },
    "required": [
        "title", "logline", "premise", "world", "tone",
        "characters", "locations", "episodes",
    ],
    "additionalProperties": False,
}

EPISODE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "screenplay": {
            "type": "string",
            "description": (
                "The episode in house format, starting with the EPISODE line. "
                "Scene headings use en dashes between fields."
            ),
        },
        "word_count_spoken": {"type": "integer"},
    },
    "required": ["screenplay", "word_count_spoken"],
    "additionalProperties": False,
}

REVIEW_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["pass", "revise"]},
        "issues": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": [
                            "timeline_contradiction", "arc_stalls", "cast_growth",
                            "runtime_overrun", "missing_hook", "continuity",
                            "unnamed_location", "undescribed_character", "format",
                        ],
                    },
                    "where": {"type": "string", "description": "Episode and scene."},
                    "problem": {"type": "string"},
                    "fix": {"type": "string"},
                },
                "required": ["kind", "where", "problem", "fix"],
                "additionalProperties": False,
            },
        },
        "summary": {"type": "string"},
    },
    "required": ["verdict", "issues", "summary"],
    "additionalProperties": False,
}

#: Loaded from the skill registry rather than inlined, so it can be
#: versioned and pinned per project. Not included in this repository.
REVIEW_SYSTEM_SKILL = "story-review"
@dataclass(slots=True)
class StoryResult:
    bible: dict[str, Any] = field(default_factory=dict)
    episodes: list[dict[str, Any]] = field(default_factory=list)
    review: dict[str, Any] = field(default_factory=dict)
    cost_usd: float = 0.0
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors and bool(self.bible)

    @property
    def screenplay(self) -> str:
        """The episodes joined into one document the parser can read."""
        return "\n\n".join(e.get("screenplay", "") for e in self.episodes).strip() + "\n"


async def write_bible(
    *,
    plot: str,
    skill: str,
    episode_count: int = 40,
    runtime_target_s: int = 90,
    tier: str = "standard",
) -> tuple[dict[str, Any], float, str | None]:
    registry = get_registry()
    provider, model = registry.resolve(
        tier,
        TaskType.LLM_WRITER,
        log_context=LogContext(agent="story-writer", target_type="project"),
    )
    request = LLMRequest(
        task=TaskType.LLM_WRITER,
        system=skill,
        user=(
            f"Build a story bible from this premise.\n\n"
            f"PREMISE:\n{plot}\n\n"
            f"Target: {episode_count} episodes of about {runtime_target_s} seconds each.\n"
            "Keep the cast tight and reuse locations — every distinct set is a build."
        ),
        json_schema=BIBLE_SCHEMA,
        schema_name="story_bible",
        effort="medium",
        max_tokens=32000,
    )
    try:
        response = await provider.complete(request, model)
    except Exception as exc:  # noqa: BLE001
        return {}, 0.0, repr(exc)
    return response.parsed or {}, response.cost_usd or 0.0, None


async def write_episode(
    *,
    bible: dict[str, Any],
    episode: dict[str, Any],
    skill: str,
    runtime_target_s: int = 90,
    tier: str = "standard",
) -> tuple[dict[str, Any], float, str | None]:
    registry = get_registry()
    provider, model = registry.resolve(
        tier,
        TaskType.LLM_WRITER,
        log_context=LogContext(agent="story-writer", target_type="episode"),
    )

    cast = "\n".join(
        f"  {c['name']} — {c['role']}. {c['physical']} Wants: {c['wants']}. "
        f"Against: {c['obstacle']}."
        for c in bible.get("characters", [])
    )
    sets = "\n".join(f"  {loc['name']}: {loc['description']}" for loc in bible.get("locations", []))

    request = LLMRequest(
        task=TaskType.LLM_WRITER,
        system=skill,
        user=(
            f"Write EPISODE {episode['number']} in house format.\n\n"
            f"WORLD: {bible.get('world', '')}\nTONE: {bible.get('tone', '')}\n\n"
            f"CAST:\n{cast}\n\nSETS (use these; do not invent new ones):\n{sets}\n\n"
            f"THIS EPISODE\nLogline: {episode['logline']}\n"
            f"Beats: {episode['beats']}\nEnds on: {episode['hook']}\n\n"
            f"Target roughly {int(runtime_target_s * 1.7)} spoken words."
        ),
        json_schema=EPISODE_SCHEMA,
        schema_name="episode",
        effort="low",
        max_tokens=16000,
    )
    try:
        response = await provider.complete(request, model)
    except Exception as exc:  # noqa: BLE001
        return {}, 0.0, repr(exc)
    parsed = response.parsed or {}
    parsed["number"] = episode["number"]
    return parsed, response.cost_usd or 0.0, None


async def review_story(
    *, bible: dict[str, Any], screenplay: str, tier: str = "standard"
) -> tuple[dict[str, Any], float, str | None]:
    registry = get_registry()
    provider, model = registry.resolve(
        tier,
        TaskType.LLM_WRITER,
        log_context=LogContext(agent="story-reviewer", target_type="project"),
    )
    request = LLMRequest(
        task=TaskType.LLM_WRITER,
        system=_system(REVIEW_SYSTEM_SKILL),
        user=(
            f"BIBLE SUMMARY\nTitle: {bible.get('title')}\n"
            f"Characters: {', '.join(c['name'] for c in bible.get('characters', []))}\n"
            f"Locations: {', '.join(loc['name'] for loc in bible.get('locations', []))}\n\n"
            f"SCREENPLAY\n{screenplay[:120000]}"
        ),
        json_schema=REVIEW_SCHEMA,
        schema_name="story_review",
        effort="medium",
        max_tokens=24000,
    )
    try:
        response = await provider.complete(request, model)
    except Exception as exc:  # noqa: BLE001
        return {}, 0.0, repr(exc)
    return response.parsed or {}, response.cost_usd or 0.0, None


async def generate_story(
    *,
    plot: str,
    skill: str,
    episode_count: int = 40,
    runtime_target_s: int = 90,
    tier: str = "standard",
    concurrency: int = 4,
    review: bool = True,
) -> StoryResult:
    """Plot → bible → episodes → review."""
    result = StoryResult()

    bible, cost, error = await write_bible(
        plot=plot, skill=skill, episode_count=episode_count,
        runtime_target_s=runtime_target_s, tier=tier,
    )
    result.cost_usd += cost
    if error:
        result.errors.append(f"bible: {error}")
        return result
    result.bible = bible

    outline = bible.get("episodes", [])
    if not outline:
        result.errors.append("bible contained no episode outline")
        return result

    log.info("story.episodes", count=len(outline))
    semaphore = asyncio.Semaphore(concurrency)

    async def one(episode: dict[str, Any]):
        async with semaphore:
            return await write_episode(
                bible=bible, episode=episode, skill=skill,
                runtime_target_s=runtime_target_s, tier=tier,
            )

    for parsed, cost, error in await asyncio.gather(*(one(e) for e in outline)):
        result.cost_usd += cost
        if error:
            result.errors.append(f"episode: {error}")
        elif parsed:
            result.episodes.append(parsed)

    result.episodes.sort(key=lambda e: e.get("number", 0))

    if review and result.episodes:
        review_out, cost, error = await review_story(
            bible=bible, screenplay=result.screenplay, tier=tier
        )
        result.cost_usd += cost
        if error:
            result.errors.append(f"review: {error}")
        else:
            result.review = review_out

    log.info(
        "story.generated",
        episodes=len(result.episodes),
        issues=len(result.review.get("issues", [])),
        cost=round(result.cost_usd, 2),
    )
    return result

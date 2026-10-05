"""Character specs.

Every character needs a written spec before a sheet can be prompted: age and
build, a face and body lock, marks, hair, signature wardrobe, props carried, a
brief, an arc, an acting master profile and a voice block.

Two of those come straight from the acting skill and are the reason it is loaded
here rather than at Stage 2: the **master profile** is one paragraph of
observable behaviour with tics that carry triggers and a mask that cracks, and
the **voice block** is one or two sentences pasted verbatim wherever the
character speaks — never paraphrased, because changing the wording widens what
the model samples from and the voice drifts.

Invention is allowed where the script is silent, but every invented detail is
marked so a reviewer can see what came from the text and what did not.
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

CHARACTER_SPEC_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "age_build": {
            "type": "string",
            "description": "Age range and physique. Body as a document of the biography.",
        },
        "face_body_lock": {
            "type": "string",
            "description": (
                "The structural description that fixes identity: bone structure, "
                "asymmetry, skin texture, age markers. Specific enough that two "
                "artists would draw the same person. Never generic AI-pretty."
            ),
        },
        "distinguishing_marks": {"type": "array", "items": {"type": "string"}},
        "hair_grooming": {"type": "string"},
        "signature_wardrobe": {
            "type": "string",
            "description": "The default costume, as one garment sentence.",
        },
        "props_carried": {"type": "array", "items": {"type": "string"}},
        "character_brief": {
            "type": "string",
            "description": "Role in the story and relationships, 2-3 sentences.",
        },
        "arc": {"type": "string", "description": "What changes across the story."},
        "acting_profile": {
            "type": "string",
            "description": (
                "One flowing paragraph, 150-220 words, observable behaviour only. "
                "Body as biography, the psychological engine, vocal profile, tics "
                "WITH their triggers, concealment behaviour, a named gait, a "
                "'However, when X...' clause where the mask cracks, and explicit "
                "eye life. No wardrobe, no camera, no colour."
            ),
        },
        "voice_block": {
            "type": "string",
            "description": (
                "1-2 sentences in quotes: age, origin/accent as a category plus "
                "1-2 phonetic markers, timbre and register, pace, and how it "
                "shifts under pressure."
            ),
        },
        "inferred": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Every detail invented rather than read from the script.",
        },
        "gaps": {
            "type": "array",
            "items": {"type": "string"},
            "description": "What a reviewer must decide before this can be generated.",
        },
    },
    "required": [
        "age_build", "face_body_lock", "distinguishing_marks", "hair_grooming",
        "signature_wardrobe", "props_carried", "character_brief", "arc",
        "acting_profile", "voice_block", "inferred", "gaps",
    ],
    "additionalProperties": False,
}

#: Loaded from the skill registry rather than inlined, so it can be
#: versioned and pinned per project. Not included in this repository.
SYSTEM_PREAMBLE_SKILL = "character-spec"
@dataclass(slots=True)
class CharacterSpec:
    character_id: str
    name: str
    tier: str | None
    spec: dict[str, Any] = field(default_factory=dict)
    model: str = ""
    cost_usd: float | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def _render_character(
    name: str,
    tier: str | None,
    scenes: list[dict[str, Any]],
    line_count: int,
) -> str:
    """Everything the script says about this character, in one place."""
    parts = [
        f"CHARACTER: {name}",
        f"Tier: {tier or 'unclassified'} — appears in {len(scenes)} scenes, "
        f"{line_count} spoken lines.",
        "",
        "EVERY MENTION IN THE SCRIPT:",
    ]
    for scene in scenes[:40]:
        bits = [f"  {scene['uid']} ({scene.get('time_of_day', '?')}, "
                f"{scene.get('location_raw', '?')})"]
        extracted = scene.get("extracted", {})
        if summary := extracted.get("action_summary"):
            bits.append(f"    {summary[:220]}")
        wardrobe = [
            cue for cue in extracted.get("wardrobe_cues", [])
            if name.split()[0].lower() in cue.lower()
        ]
        states = [
            cue for cue in extracted.get("state_cues", [])
            if name.split()[0].lower() in cue.lower()
        ]
        if wardrobe:
            bits.append(f"    WARDROBE: {'; '.join(wardrobe)}")
        if states:
            bits.append(f"    STATE: {'; '.join(states)}")
        for line in scene.get("dialogue_sample", [])[:3]:
            bits.append(f"    SAYS: {line}")
        parts.append("\n".join(bits))
    if len(scenes) > 40:
        parts.append(f"  …and {len(scenes) - 40} further scenes.")
    return "\n".join(parts)


async def write_spec(
    *,
    character_id: str,
    name: str,
    tier: str | None,
    scenes: list[dict[str, Any]],
    line_count: int,
    acting_skill: str,
    tier_name: str = "standard",
) -> CharacterSpec:
    registry = get_registry()
    provider, model = registry.resolve(

        tier_name,

        TaskType.LLM_WRITER,

        log_context=LogContext(agent="spec-writer", target_type="character"),

    )
    result = CharacterSpec(character_id=character_id, name=name, tier=tier)

    request = LLMRequest(
        task=TaskType.LLM_WRITER,
        system=f"{_system(SYSTEM_PREAMBLE_SKILL)}\n\n--- THE ACTING SYSTEM ---\n\n{acting_skill}",
        user=_render_character(name, tier, scenes, line_count),
        json_schema=CHARACTER_SPEC_SCHEMA,
        schema_name="character_spec",
        effort="medium",
        max_tokens=8192,
    )

    try:
        response = await provider.complete(request, model)
    except Exception as exc:  # noqa: BLE001
        result.error = repr(exc)
        return result

    result.spec = response.parsed or {}
    result.model = response.model
    result.cost_usd = response.cost_usd
    return result


async def write_specs(
    characters: list[dict[str, Any]],
    *,
    acting_skill: str,
    tier_name: str = "standard",
    concurrency: int = 4,
) -> list[CharacterSpec]:
    semaphore = asyncio.Semaphore(concurrency)

    async def one(entry: dict[str, Any]) -> CharacterSpec:
        async with semaphore:
            return await write_spec(
                character_id=entry["id"],
                name=entry["name"],
                tier=entry.get("tier"),
                scenes=entry.get("scenes", []),
                line_count=entry.get("line_count", 0),
                acting_skill=acting_skill,
                tier_name=tier_name,
            )

    results = await asyncio.gather(*(one(c) for c in characters))
    log.info(
        "specs.written",
        total=len(results),
        failed=sum(1 for r in results if not r.ok),
    )
    return results

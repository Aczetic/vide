"""Location and prop specs, plate prompts, and their rule checks.

Locations are two-tier. The **geography master** is 16:9 and teaches the
model the whole room; it is never used as a first frame. **Coverage plates** are
in the delivery ratio and are what actually seed a shot. The master is generated
first because every coverage plate inherits it — reversing that order produces a
set of unrelated rooms.

Lighting states are edits of the base plate, never regenerations. A
night version of a room that was generated fresh is a different room.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import structlog

from vide.providers import LLMRequest, LogContext, SchemaViolation, TaskType, get_registry

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

# -- rules that must hold on a plate prompt -----------------------------------

_PLATE_FORBIDDEN: tuple[tuple[str, str], ...] = (
    (
        r"\b(man|woman|person|people|figure|character|someone|crowd|extras?)\b",
        "plates are generated empty — people are separate assets that arrive at "
        "shot time, and a person baked into a plate cannot be moved or removed",
    ),
    (
        r"\b(gun|pistol|rifle|knife|weapon|blade)\b",
        "no weapons in a plate — they are props with their own asset",
    ),
    (
        r"\bf/\d|\bdepth of field\b|\bbokeh\b|\bshallow focus\b|\d+\s*mm\b",
        "optics and depth-of-field language belong on characters, not locations "
        "— a plate with baked-in focus cannot be reframed",
    ),
    (
        r"\bfrontal\b|\bstraight[- ]on\b|\bhead[- ]on view\b",
        "a frontal plate is flat wallpaper: the model cannot read volume and "
        "invents new surroundings past the frame edge",
    ),
    (r"--ar\b|\b\d{1,2}:\d{1,2}\b|\b[24]k\b", "aspect and resolution are provider parameters"),
)

_PLATE_REQUIRED: tuple[tuple[str, str], ...] = (
    (
        r"three[- ]quarter|3/4|corner|diagonal|angled",
        "a three-quarter or angled view, so the model can read volume",
    ),
    (r"empty|deserted|unoccupied|no one|bare", "state the emptiness positively"),
)

_PROP_FORBIDDEN: tuple[tuple[str, str], ...] = (
    (r"--ar\b|\b\d{1,2}:\d{1,2}\b|\b[24]k\b", "aspect and resolution are provider parameters"),
    (
        r"\b(nike|adidas|apple|iphone|samsung|coca[- ]?cola|rolex|bmw|mercedes)\b",
        "no brand names — state 'plain unbranded surface' positively instead",
    ),
)

_PROP_REQUIRED: tuple[tuple[str, str], ...] = (
    (r"neutral|plain|seamless|isolated", "a neutral backdrop with the subject isolated"),
)

#: Words that trip provider safety filters on otherwise ordinary props. The
#: skill's advice is to describe by neutral materials and function instead.
_TRIGGER_WORDS = {
    "gun": "compact metal handheld device with a grip and barrel housing",
    "pistol": "compact metal handheld device with a grip and barrel housing",
    "rifle": "long metal handheld device with a shoulder stock",
    "weapon": "metal handheld object",
    "knife": "short steel blade with a riveted handle",
    "blood": "dark red viscous liquid",
    "corpse": "still figure",
    "drug": "small pressed tablet",
}


def check_plate(prompt: str) -> list[str]:
    return _check(prompt, _PLATE_FORBIDDEN, _PLATE_REQUIRED)


def check_prop(prompt: str) -> list[str]:
    return _check(prompt, _PROP_FORBIDDEN, _PROP_REQUIRED)


def _check(prompt: str, forbidden, required) -> list[str]:
    violations = []
    lowered = prompt.lower()
    for pattern, reason in forbidden:
        match = re.search(pattern, lowered)
        if match:
            violations.append(f"contains {match.group(0)!r} — {reason}")
    for pattern, reason in required:
        if not re.search(pattern, lowered):
            violations.append(f"missing: {reason}")
    if len(prompt) > 2000:
        violations.append(f"{len(prompt)} characters — past 2000 details start dropping")
    return violations


def neutralise(text: str) -> str:
    """Rewrite words that trip safety filters, preserving the object.

    A prop sheet for a gun is ordinary production work, but the word reliably
    gets a generation refused. Describing it by material and function gets the
    same object through.
    """
    out = text
    for word, replacement in _TRIGGER_WORDS.items():
        out = re.sub(rf"\b{word}s?\b", replacement, out, flags=re.IGNORECASE)
    return out


# -- specs --------------------------------------------------------------------

LOCATION_SPEC_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "geography_brief": {
            "type": "string",
            "description": (
                "The written architecture: what is in the room, its materials "
                "and rough dimensions, where the openings are. A set-dec note."
            ),
        },
        "anchor_objects": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Two or three fixed, nameable things to stage against.",
        },
        "light_source": {
            "type": "string",
            "description": "One source, one direction, and what it does to the room.",
        },
        "colour_world": {"type": "string", "description": "Which register this set belongs to."},
        "palette": {
            "type": "string",
            "description": "Base field, accents with their real in-frame source, counter-note.",
        },
        "hero_props": {"type": "array", "items": {"type": "string"}},
        "era_constraints": {"type": "string"},
        "continuity_risk": {
            "type": "string",
            "description": "What goes wrong across episodes if this set drifts.",
        },
        "times_of_day": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Lighting states the script needs. The first is the base.",
        },
        "inferred": {"type": "array", "items": {"type": "string"}},
        "gaps": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "geography_brief", "anchor_objects", "light_source", "colour_world",
        "palette", "hero_props", "era_constraints", "continuity_risk",
        "times_of_day", "inferred", "gaps",
    ],
    "additionalProperties": False,
}

PLATE_PROMPT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "camera_anchor": {
            "type": "string",
            "description": (
                "Simple and concrete: 'high-angle three-quarter wide shot, camera "
                "high above the room looking diagonally down at 45 degrees'. "
                "Abstract jargon fails."
            ),
        },
        "architecture": {"type": "string"},
        "surfaces": {
            "type": "string",
            "description": "Real wear: rust, cracks, tape, fingerprints, water marks.",
        },
        "light": {"type": "string"},
        "palette": {"type": "string"},
        "full_prompt": {
            "type": "string",
            "description": "One natural-prose prompt, 80-150 words, under 1500 characters.",
        },
    },
    "required": ["camera_anchor", "architecture", "surfaces", "light", "palette", "full_prompt"],
    "additionalProperties": False,
}

PROP_PROMPT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "subject": {"type": "string"},
        "materials": {"type": "string", "description": "Material plus finish plus wear state."},
        "text_on_object": {
            "type": "string",
            "description": (
                "Exact copy in quotes with font, weight and colour, or an empty "
                "string. Vague 'add text' smears."
            ),
        },
        "full_prompt": {"type": "string"},
    },
    "required": ["subject", "materials", "text_on_object", "full_prompt"],
    "additionalProperties": False,
}

#: Loaded from the skill registry rather than inlined, so it can be
#: versioned and pinned per project. Not included in this repository.
PLATE_SYSTEM_SKILL = "location-plate-prompt"
#: Loaded from the skill registry rather than inlined, so it can be
#: versioned and pinned per project. Not included in this repository.
PROP_SYSTEM_SKILL = "prop-sheet-prompt"
@dataclass(slots=True)
class SpecResult:
    entity_id: str
    name: str
    spec: dict[str, Any] = field(default_factory=dict)
    cost_usd: float | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


@dataclass(slots=True)
class PlatePrompt:
    entity_id: str
    name: str
    variant_label: str
    prompt: str = ""
    sections: dict[str, str] = field(default_factory=dict)
    violations: list[str] = field(default_factory=list)
    cost_usd: float | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and not self.violations


async def _complete(request: LLMRequest, tier: str):
    registry = get_registry()
    provider, model = registry.resolve(

        tier,

        TaskType.LLM_WRITER,

        log_context=LogContext(agent="location-spec-writer", target_type="asset"),

    )
    try:
        return await provider.complete(request, model)
    except SchemaViolation:
        request.max_tokens = 24000
        return await provider.complete(request, model)


async def write_location_spec(
    *,
    entity_id: str,
    name: str,
    scenes: list[dict[str, Any]],
    skill: str,
    era: str | None = None,
    tier: str = "standard",
) -> SpecResult:
    result = SpecResult(entity_id=entity_id, name=name)
    lines = [f"SET: {name}", f"Appears in {len(scenes)} scenes.", ""]
    if era:
        lines.append(f"PERIOD: {era} — nothing in frame may be newer.\n")
    for scene in scenes[:30]:
        extracted = scene.get("extracted", {})
        lines.append(
            f"  {scene['uid']} ({scene.get('time_of_day', '?')}): "
            f"{(extracted.get('action_summary') or '')[:200]}"
        )
        if props := extracted.get("props"):
            lines.append(f"    props present: {', '.join(props[:8])}")

    request = LLMRequest(
        task=TaskType.LLM_WRITER,
        system=skill,
        user="\n".join(lines),
        json_schema=LOCATION_SPEC_SCHEMA,
        schema_name="location_spec",
        effort="low",
        max_tokens=16000,
    )
    try:
        response = await _complete(request, tier)
    except Exception as exc:  # noqa: BLE001
        result.error = repr(exc)
        return result
    result.spec = response.parsed or {}
    result.cost_usd = response.cost_usd
    return result


async def write_plate_prompt(
    *,
    entity_id: str,
    name: str,
    spec: dict[str, Any],
    image_skill: str,
    time_of_day: str = "day",
    is_master: bool = True,
    era: str | None = None,
    tier: str = "standard",
) -> PlatePrompt:
    label = "geography master" if is_master else f"coverage plate — {time_of_day.lower()}"
    result = PlatePrompt(entity_id=entity_id, name=name, variant_label=label)

    tier_note = (
        "GEOGRAPHY MASTER — the whole room, the widest readable view. This is "
        "never used as a first frame; every coverage plate inherits the room "
        "from it."
        if is_master
        else "COVERAGE PLATE — the delivery ratio, the view a shot is seeded from."
    )
    brief = (
        f"Write a plate prompt for: {name}\n"
        f"This is the {tier_note}\n"
        f"Time of day: {time_of_day}\n\n"
        f"Geography: {spec.get('geography_brief', 'not described')}\n"
        f"Anchor objects: {', '.join(spec.get('anchor_objects', [])) or 'none named'}\n"
        f"Light: {spec.get('light_source', 'unstated')}\n"
        f"Palette: {spec.get('palette', 'unstated')}\n"
        f"Hero props: {', '.join(spec.get('hero_props', [])) or 'none'}\n"
        + (f"Period: {era} — nothing newer may appear.\n" if era else "")
    )

    request = LLMRequest(
        task=TaskType.LLM_WRITER,
        system=f"{_system(PLATE_SYSTEM_SKILL)}\n\n--- IMAGE PROMPT SKILL ---\n\n{image_skill}",
        user=brief,
        json_schema=PLATE_PROMPT_SCHEMA,
        schema_name="plate_prompt",
        effort="low",
        max_tokens=16000,
    )
    try:
        response = await _complete(request, tier)
    except Exception as exc:  # noqa: BLE001
        result.error = repr(exc)
        return result

    parsed = response.parsed or {}
    result.prompt = parsed.get("full_prompt", "")
    result.sections = {k: v for k, v in parsed.items() if k != "full_prompt"}
    result.cost_usd = response.cost_usd
    result.violations = check_plate(result.prompt)
    return result


async def write_prop_prompt(
    *,
    entity_id: str,
    name: str,
    scenes: list[dict[str, Any]],
    image_skill: str,
    state: str = "default",
    tier: str = "standard",
) -> PlatePrompt:
    result = PlatePrompt(
        entity_id=entity_id, name=name, variant_label=f"prop sheet — {state}"
    )
    context = "\n".join(
        f"  {s['uid']}: {(s.get('extracted', {}).get('action_summary') or '')[:160]}"
        for s in scenes[:10]
    )
    request = LLMRequest(
        task=TaskType.LLM_WRITER,
        system=f"{_system(PROP_SYSTEM_SKILL)}\n\n--- IMAGE PROMPT SKILL ---\n\n{image_skill}",
        user=f"PROP: {name}\nState: {state}\n\nSeen in:\n{context}",
        json_schema=PROP_PROMPT_SCHEMA,
        schema_name="prop_prompt",
        effort="low",
        max_tokens=12000,
    )
    try:
        response = await _complete(request, tier)
    except Exception as exc:  # noqa: BLE001
        result.error = repr(exc)
        return result

    parsed = response.parsed or {}
    # Neutralise before checking: the object survives, the trigger word does not.
    result.prompt = neutralise(parsed.get("full_prompt", ""))
    result.sections = {k: v for k, v in parsed.items() if k != "full_prompt"}
    result.cost_usd = response.cost_usd
    result.violations = check_prop(result.prompt)
    return result

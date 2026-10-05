"""Story-day inference.

The in-story day a scene happens on. It drives costume continuity: a character
wears one outfit per story-day unless the text changes it, so the costume build
count is roughly characters × story-days.

**Day boundaries are computed, not asked for.** An earlier version put the whole
job to the model and returned 6 days on one run, 4 on the next and 5 on a third
from identical input. That number multiplies into the build count, so it cannot
wobble: a title is not allowed to need 96 costumes on Monday and 64 on Tuesday.

What the model is used for instead is reading the *dialogue* for time references
a rule cannot see — "yesterday", "three days ago", "since the funeral". Those
come back as proposals attached to specific scenes and become G1 questions. A
human confirms day boundaries at G1 regardless, so the honest shape is a
stable deterministic answer plus a list of things worth a second look.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import structlog

from vide.providers import LLMRequest, LogContext, TaskType, get_registry

log = structlog.get_logger(__name__)

#: Rough position in a day. A scene whose slot is earlier than the previous
#: scene's has crossed midnight.
_DAY_ORDER = {
    "DAWN": 0, "MORNING": 1, "DAY": 2, "AFTERNOON": 3,
    "EVENING": 4, "DUSK": 4, "NIGHT": 5,
}
#: These carry no information about position in the day.
_NEUTRAL = {"CONTINUOUS", "LATER", "SAME TIME", "UNSPECIFIED", None, ""}


@dataclass(slots=True)
class StoryDay:
    day: int
    first_scene: str
    last_scene: str
    evidence: str
    confidence: str
    scenes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class StoryDayResult:
    days: list[StoryDay] = field(default_factory=list)
    contradictions: list[dict[str, Any]] = field(default_factory=list)
    #: Signals the model found in dialogue. Proposals for G1, not applied.
    proposals: list[dict[str, Any]] = field(default_factory=list)
    scene_to_day: dict[str, int] = field(default_factory=dict)
    cost_usd: float = 0.0
    error: str | None = None


def _boundary_reason(previous: dict[str, Any], current: dict[str, Any]) -> str | None:
    """Does a new story-day start at `current`? Returns the reason, or None."""
    if current.get("time_jump_before"):
        return f"explicit time jump: {current['time_jump_before']!r}"

    previous_time = (previous.get("time_of_day") or "").upper()
    current_time = (current.get("time_of_day") or "").upper()
    if previous_time in _NEUTRAL or current_time in _NEUTRAL:
        return None

    previous_slot = _DAY_ORDER.get(previous_time)
    current_slot = _DAY_ORDER.get(current_time)
    if previous_slot is None or current_slot is None:
        return None

    # Night then morning is a new day. Day then night is the same one.
    if current_slot < previous_slot and previous_slot >= _DAY_ORDER["EVENING"]:
        return f"{previous_time.lower()} then {current_time.lower()} — crosses midnight"
    return None


def assign_days(breakdowns: list[dict[str, Any]]) -> tuple[list[StoryDay], dict[str, int]]:
    """Deterministic day assignment. Same input, same output, every time."""
    if not breakdowns:
        return [], {}

    days: list[StoryDay] = []
    scene_to_day: dict[str, int] = {}

    current = StoryDay(
        day=1,
        first_scene=breakdowns[0]["uid"],
        last_scene=breakdowns[0]["uid"],
        evidence="first scene of the script",
        confidence="certain",
        scenes=[breakdowns[0]["uid"]],
    )
    scene_to_day[breakdowns[0]["uid"]] = 1

    # Deliberately offset by one to compare each scene with its predecessor,
    # so the two sequences differ in length — strict= would reject that.
    for previous, row in zip(breakdowns, breakdowns[1:], strict=False):
        uid = row["uid"]
        reason = _boundary_reason(previous, row)
        if reason:
            current.last_scene = previous["uid"]
            days.append(current)
            current = StoryDay(
                day=current.day + 1,
                first_scene=uid,
                last_scene=uid,
                evidence=reason,
                # An explicit marker is certain; a time-of-day inference is not.
                confidence="certain" if row.get("time_jump_before") else "likely",
                scenes=[uid],
            )
        else:
            current.scenes.append(uid)
        scene_to_day[uid] = current.day

    current.last_scene = breakdowns[-1]["uid"]
    days.append(current)
    return days, scene_to_day


PROPOSAL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "proposals": {
            "type": "array",
            "description": (
                "Time references in dialogue or action that suggest the computed "
                "day boundaries are wrong. Do not restate boundaries that are "
                "already correct."
            ),
            "items": {
                "type": "object",
                "properties": {
                    "scene": {"type": "string"},
                    "quote": {"type": "string", "description": "The phrase itself."},
                    "implication": {"type": "string"},
                    "suggested_change": {
                        "type": "string",
                        "enum": ["split_before", "merge_with_previous", "none"],
                    },
                },
                "required": ["scene", "quote", "implication", "suggested_change"],
                "additionalProperties": False,
            },
        },
        "contradictions": {
            "type": "array",
            "description": "Places where the script's own time references disagree.",
            "items": {
                "type": "object",
                "properties": {
                    "scenes": {"type": "array", "items": {"type": "string"}},
                    "problem": {"type": "string"},
                },
                "required": ["scenes", "problem"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["proposals", "contradictions"],
    "additionalProperties": False,
}

SYSTEM = """\
Story-days have already been assigned by rule: a new day starts at an explicit
time-jump marker, or where the time of day crosses midnight (night, then
morning). That assignment is stable and is not yours to change.

Your job is the part a rule cannot do: read the scene summaries and story-day
hints for time references in the *text* — "yesterday", "three days ago", "since
the wedding", "it's been a week" — and say where they contradict the computed
boundaries.

Report only genuine disagreements. If a scene's text is consistent with the day
it was given, say nothing about it. A long list of restated boundaries is worse
than a short list of real problems, because it trains a reviewer to skim.

Also report contradictions *within* the script: if dialogue calls something
yesterday that the structure puts sixteen days back, the script has a problem.
Say so. Do not pick a reading.
"""


def _render(breakdowns: list[dict[str, Any]], scene_to_day: dict[str, int]) -> str:
    lines = []
    for row in breakdowns:
        uid = row["uid"]
        bits = [f"{uid:<8} D{scene_to_day.get(uid, '?'):<3} {row.get('time_of_day') or '?':<8}"]
        if hint := row.get("extracted", {}).get("story_day_hint"):
            bits.append(f"HINT: {hint}")
        bits.append(f"| {(row.get('extracted', {}).get('action_summary') or '')[:110]}")
        lines.append("  ".join(bits))
    return "\n".join(lines)


async def infer_story_days(
    breakdowns: list[dict[str, Any]], *, tier: str = "standard"
) -> StoryDayResult:
    days, scene_to_day = assign_days(breakdowns)
    result = StoryDayResult(days=days, scene_to_day=scene_to_day)

    if not breakdowns:
        return result

    registry = get_registry()
    provider, model = registry.resolve(

        tier,

        TaskType.LLM_WRITER,

        log_context=LogContext(agent="story-day-reviewer", target_type="project"),

    )

    request = LLMRequest(
        task=TaskType.LLM_WRITER,
        system=SYSTEM,
        user=(
            f"{len(breakdowns)} scenes with their computed story-day.\n\n"
            f"{_render(breakdowns, scene_to_day)}"
        ),
        json_schema=PROPOSAL_SCHEMA,
        schema_name="story_day_review",
        effort="low",
        max_tokens=16000,
    )

    try:
        response = await provider.complete(request, model)
        parsed = response.parsed or {}
        result.cost_usd = response.cost_usd or 0.0
        result.proposals = [
            p for p in parsed.get("proposals", []) if p.get("suggested_change") != "none"
        ]
        result.contradictions = parsed.get("contradictions", [])
    except Exception as exc:  # noqa: BLE001
        # The review failing does not invalidate the day assignment — the
        # boundaries are computed, not generated. Record it and carry on.
        result.error = repr(exc)
        log.warning("story_days.review_failed", error=str(exc)[:200])

    log.info(
        "story_days.assigned",
        days=len(result.days),
        scenes=len(result.scene_to_day),
        proposals=len(result.proposals),
        contradictions=len(result.contradictions),
    )
    return result


def to_json(result: StoryDayResult) -> str:
    return json.dumps(
        {
            "days": [
                {
                    "day": d.day,
                    "first_scene": d.first_scene,
                    "last_scene": d.last_scene,
                    "scene_count": len(d.scenes),
                    "evidence": d.evidence,
                    "confidence": d.confidence,
                }
                for d in result.days
            ],
            "proposals": result.proposals,
            "contradictions": result.contradictions,
            "scene_to_day": result.scene_to_day,
        },
        indent=2,
    )

"""Per-scene breakdown extraction.

One call per scene, never the whole script — a model given forty episodes at
once misses things quietly, and the cost of a missed set is discovered at shot
list time.

**The model is not asked to re-extract what the parser already knows.** Episode,
scene number, INT/EXT, location, time of day, the heading cast list and the
dialogue are parsed deterministically and passed in as settled context. Asking
for them again would be slower, costlier, and would introduce a class of error
that cannot otherwise happen: the model disagreeing with the parser about which
scene this is. What is asked for is the layer that needs judgement — what the
text *implies* a production has to build.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import structlog

from vide.pipeline.ingest import ParsedScene
from vide.providers import LLMRequest, LogContext, SchemaViolation, TaskType, get_registry

log = structlog.get_logger(__name__)


def _string_list(description: str) -> dict[str, Any]:
    return {
        "type": "array",
        "items": {"type": "string"},
        "description": description,
    }


#: The record, minus the fields the parser already settled.
BREAKDOWN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "characters_present": {
            "type": "array",
            "description": (
                "Everyone physically in the scene, including non-speakers and "
                "people named only in action lines."
            ),
            "items": {
                "type": "object",
                "properties": {
                    "name_raw": {
                        "type": "string",
                        "description": "As the script writes it. Do not normalise.",
                    },
                    "speaks": {"type": "boolean"},
                    "from_action_line": {
                        "type": "boolean",
                        "description": "True when absent from the heading cast list.",
                    },
                },
                "required": ["name_raw", "speaks", "from_action_line"],
                "additionalProperties": False,
            },
        },
        "implied_locations": {
            "type": "array",
            "description": (
                "Places the action requires that the heading does not name — the "
                "most commonly missed item in a breakdown."
            ),
            "items": {
                "type": "object",
                "properties": {
                    "name_raw": {"type": "string"},
                    "evidence": {
                        "type": "string",
                        "description": "The phrase in the action that implies it.",
                    },
                },
                "required": ["name_raw", "evidence"],
                "additionalProperties": False,
            },
        },
        "props": _string_list("Objects handled, referenced, or needed by the action."),
        "vehicles": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name_raw": {"type": "string"},
                    "interior_seen": {
                        "type": "boolean",
                        "description": "A car interior is a separate asset from the exterior.",
                    },
                },
                "required": ["name_raw", "interior_seen"],
                "additionalProperties": False,
            },
        },
        "screens_inserts": _string_list(
            "Phone screens, signs, letters, tickers, photographs — legible "
            "graphics that are real design work."
        ),
        "wardrobe_cues": _string_list("What the text says anyone is wearing."),
        "state_cues": _string_list(
            "Physical condition a later scene must match: injuries, blood, wet, "
            "dirty, unshaven."
        ),
        "story_day_hint": {
            "type": ["string", "null"],
            "description": (
                "Quoted textual signal about when this happens relative to other "
                "scenes. Null when the text gives none — do not guess."
            ),
        },
        "action_summary": {
            "type": "string",
            "description": "Two or three sentences: what physically happens, in order.",
        },
        "emotional_beat": {
            "type": "string",
            "description": "One line: what shifts in this scene.",
        },
        "gaps": _string_list(
            "What a production needs that the text does not supply, each phrased "
            "as the missing decision."
        ),
    },
    "required": [
        "characters_present", "implied_locations", "props", "vehicles",
        "screens_inserts", "wardrobe_cues", "state_cues", "story_day_hint",
        "action_summary", "emotional_beat", "gaps",
    ],
    "additionalProperties": False,
}


@dataclass(slots=True)
class SceneBreakdown:
    """One scene's breakdown: parsed facts plus extracted judgement."""

    episode: int
    scene_label: str
    #: Unique scene key; episode+label alone collide on intercut pairs.
    uid: str
    #: The deterministic half.
    int_ext: str | None
    location_raw: str | None
    sub_area_raw: str | None
    time_of_day: str | None
    heading_cast: list[str]
    source_span: dict[str, int]
    #: The extracted half.
    extracted: dict[str, Any] = field(default_factory=dict)
    #: Provenance for the generation log.
    model: str = ""
    skill_versions: dict[str, int] = field(default_factory=dict)
    tokens_in: int | None = None
    tokens_out: int | None = None
    cost_usd: float | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None

    @property
    def gaps(self) -> list[str]:
        return list(self.extracted.get("gaps", []))

    def character_names(self) -> list[str]:
        """Heading cast plus anyone the model found in the action."""
        names = list(self.heading_cast)
        for entry in self.extracted.get("characters_present", []):
            name = entry.get("name_raw", "").strip()
            if name and name.upper() not in {n.upper() for n in names}:
                names.append(name)
        return names


def _render_scene(scene: ParsedScene) -> str:
    """The scene as the model should read it: settled facts, then the text."""
    facts = [
        f"Episode {scene.episode_number}, scene {scene.number_label}",
        f"INT/EXT: {scene.int_ext or 'unstated'}",
        f"Location (from heading): {scene.location_raw or 'unstated'}",
    ]
    if scene.sub_area_raw:
        facts.append(f"Sub-area (from heading): {scene.sub_area_raw}")
    facts.append(f"Time of day: {scene.time_of_day or 'unstated'}")
    facts.append(
        f"Cast listed in heading: {', '.join(scene.characters_raw) or 'none listed'}"
    )
    if scene.time_jump_before:
        facts.append(f"A time-jump marker precedes this scene: {scene.time_jump_before!r}")
    if scene.intercut_group:
        facts.append(
            "This scene is intercut with another — they play simultaneously."
        )

    parts = [
        "THESE FACTS ARE SETTLED. Do not restate or contradict them:",
        *(f"  {fact}" for fact in facts),
        "\nSCENE TEXT:\n",
    ]
    parts.append(scene.heading_raw)
    parts.extend(scene.action_lines)
    for line in scene.dialogue:
        cue = f" ({line.delivery_raw})" if line.delivery_raw else ""
        parts.append(f"{line.speaker_raw}{cue}: {line.line}")
    return "\n".join(parts)


async def extract_scene(
    scene: ParsedScene,
    *,
    skill_text: str,
    skill_versions: dict[str, int],
    tier: str = "standard",
) -> SceneBreakdown:
    registry = get_registry()
    provider, model = registry.resolve(

        tier,

        TaskType.LLM_WRITER,

        log_context=LogContext(agent="breakdown-extractor", target_type="scene"),

    )

    result_obj = SceneBreakdown(
        episode=scene.episode_number,
        scene_label=scene.number_label,
        uid=scene.uid,
        int_ext=scene.int_ext,
        location_raw=scene.location_raw,
        sub_area_raw=scene.sub_area_raw,
        time_of_day=scene.time_of_day,
        heading_cast=list(scene.characters_raw),
        source_span={"start_line": scene.start_line, "end_line": scene.end_line},
        skill_versions=dict(skill_versions),
    )

    request = LLMRequest(
        task=TaskType.LLM_WRITER,
        system=skill_text,
        user=_render_scene(scene),
        json_schema=BREAKDOWN_SCHEMA,
        schema_name="scene_breakdown",
        effort=registry.effort_for(tier),
        max_tokens=4096,
    )

    try:
        response = await provider.complete(request, model)
    except SchemaViolation as exc:
        # A truncated response is recoverable: thinking consumed the budget, so
        # retry once with reasoning turned down and more room to write. Losing a
        # scene silently means a missing set discovered at shot-list time.
        log.warning(
            "breakdown.retry", scene=scene.uid, reason="schema violation", detail=str(exc)[:160]
        )
        request.effort = "low"
        request.max_tokens = 8192
        try:
            response = await provider.complete(request, model)
        except Exception as retry_exc:  # noqa: BLE001
            result_obj.error = f"failed twice: {retry_exc}"
            return result_obj
    except Exception as exc:  # noqa: BLE001 — the failure belongs on the record
        result_obj.error = repr(exc)
        return result_obj

    result_obj.extracted = response.parsed or {}
    result_obj.model = response.model
    result_obj.tokens_in = response.tokens_in
    result_obj.tokens_out = response.tokens_out
    result_obj.cost_usd = response.cost_usd
    return result_obj


async def extract_all(
    scenes: list[ParsedScene],
    *,
    skill_text: str,
    skill_versions: dict[str, int],
    tier: str = "standard",
    concurrency: int = 6,
    sweeps: int = 2,
) -> list[SceneBreakdown]:
    """Fan out across scenes (principle 1: parallelise what can parallelise).

    Concurrency is capped so a 100-episode script does not open three hundred
    simultaneous connections and trip a provider rate limit.

    Failed scenes are swept again after the batch. A transport error — a dropped
    connection, a DNS blip, a laptop that slept — kills a scene mid-run, and the
    per-call retries all fail inside the same bad window. Coming back once the
    batch has drained usually succeeds, and a missing scene means a missing set
    discovered at shot-list time.
    """
    semaphore = asyncio.Semaphore(concurrency)
    done = 0

    async def one(scene: ParsedScene) -> SceneBreakdown:
        nonlocal done
        async with semaphore:
            result = await extract_scene(
                scene,
                skill_text=skill_text,
                skill_versions=skill_versions,
                tier=tier,
            )
        done += 1
        if done % 10 == 0 or done == len(scenes):
            log.info("breakdown.progress", done=done, total=len(scenes))
        return result

    results = list(await asyncio.gather(*(one(s) for s in scenes)))
    by_uid = {scene.uid: scene for scene in scenes}

    for sweep in range(1, sweeps):
        failed = [(i, r) for i, r in enumerate(results) if not r.ok]
        if not failed:
            break
        log.warning("breakdown.sweep", sweep=sweep, retrying=len(failed))
        # Give a flapping network a moment to settle before trying again.
        await asyncio.sleep(5 * sweep)
        done = 0
        retried = await asyncio.gather(
            *(one(by_uid[r.uid]) for _, r in failed)
        )
        for (index, _), result in zip(failed, retried, strict=True):
            if result.ok:
                results[index] = result

    return results

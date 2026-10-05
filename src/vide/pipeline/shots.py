"""Shot planning and keyframes.

A scene becomes a shot list; each shot gets a four-group card; each card becomes
a keyframe. The keyframe is the point of Stage 1 — it is the anchor Stage 2
generates video from, and the storyboard a client approves before any video
spend.

Two things here are not optional:

**The spatial map is written once per scene and pasted unchanged into every
shot of it.** Writing it per shot produces a room that rearranges itself between
cuts, which is the most expensive failure in generated footage.

**A shot cannot be keyframed until every asset it references is approved**
(principle 3). Not generated — *approved*. Composing a keyframe from a character
sheet nobody has signed off means regenerating every shot that used it when the
sheet changes.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Any

import structlog
from sqlalchemy import select

from vide.db import session_scope
from vide.models.asset import Asset, Shot, ShotAssetRef
from vide.models.core import Episode, Scene, SceneCharacter
from vide.models.entity import Character
from vide.models.enums import AssetKind, Complexity, ElementStatus
from vide.pipeline.generate import GenerationBatch, generate_candidates
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

SHOT_LIST_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "spatial_map": {
            "type": "string",
            "description": (
                "The scene's geography, written once. Camera side, the 180 line, "
                "landmark positions, who stands at which landmark, exact "
                "headcount. Pasted unchanged into every shot."
            ),
        },
        "shots": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "letter": {"type": "string", "description": "A, B, C…"},
                    "is_master_wide": {
                        "type": "boolean",
                        "description": "True for the ~1s opening wide that fixes blocking.",
                    },
                    "duration_s": {"type": "number"},
                    "complexity": {"type": "string", "enum": ["simple", "medium", "complex"]},
                    "characters": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Canonical names of everyone in frame.",
                    },
                    "action": {"type": "string", "description": "One to three sentences."},
                    "lines": {"type": "string", "description": "Dialogue verbatim, or empty."},
                    "goal": {"type": "string", "description": "The shot's purpose, one line."},
                    "tasks": {
                        "type": "string",
                        "description": "Each character's task as a verb aimed at someone.",
                    },
                    "dramaturgy": {"type": "string", "description": "What changes, start to end."},
                    "blocking": {"type": "string", "description": "Positions relative to camera."},
                    "acting": {
                        "type": "string",
                        "description": "What face and body do, and what is hidden.",
                    },
                    "shot_size": {"type": "string"},
                    "lens_fov_deg": {
                        "type": "integer",
                        "description": "Field of view in degrees. One lens per shot.",
                    },
                    "camera_angle": {"type": "string"},
                    "camera_height": {"type": "string"},
                    "camera_movement": {"type": "string"},
                    "cut_type": {"type": "string"},
                    "hooks_into_next": {"type": "string"},
                },
                "required": [
                    "letter", "is_master_wide", "duration_s", "complexity",
                    "characters", "action", "lines", "goal", "tasks",
                    "dramaturgy", "blocking", "acting", "shot_size",
                    "lens_fov_deg", "camera_angle", "camera_height",
                    "camera_movement", "cut_type", "hooks_into_next",
                ],
                "additionalProperties": False,
            },
        },
        "missing_assets": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Anything in frame with no asset. A shot without one cannot be made.",
        },
    },
    "required": ["spatial_map", "shots", "missing_assets"],
    "additionalProperties": False,
}

KEYFRAME_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "full_prompt": {
            "type": "string",
            "description": (
                "The still, composed from the approved assets plus the card's "
                "camera, blocking and light. Characters already mid-action — "
                "states, not transitions."
            ),
        },
        "first_frame": {"type": "string", "description": "Who is where, in frame one."},
        "optics": {"type": "string"},
        "lighting": {"type": "string"},
    },
    "required": ["full_prompt", "first_frame", "optics", "lighting"],
    "additionalProperties": False,
}

#: Loaded from the skill registry rather than inlined, so it can be
#: versioned and pinned per project. Not included in this repository.
KEYFRAME_SYSTEM_SKILL = "keyframe-prompt"
@dataclass(slots=True)
class PlannedShot:
    letter: str
    card: dict[str, Any]
    shot_id: uuid.UUID | None = None


@dataclass(slots=True)
class SceneShots:
    scene_id: uuid.UUID
    label: str
    spatial_map: str = ""
    shots: list[PlannedShot] = field(default_factory=list)
    missing_assets: list[str] = field(default_factory=list)
    cost_usd: float = 0.0
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and bool(self.shots)


def _approved_assets(session, project_id: uuid.UUID) -> dict[str, dict]:
    """Approved assets by display name. Only approved ones may seed a keyframe."""
    out: dict[str, dict] = {}
    for asset in session.scalars(
        select(Asset).where(
            Asset.project_id == project_id,
            Asset.status == ElementStatus.APPROVED,
        )
    ):
        name = (asset.descriptor_text or "").strip()
        if name:
            out[name.lower()] = {
                "tag": asset.tag,
                "descriptor": asset.descriptor_text,
                "variant": asset.variant_label,
                "kind": str(asset.kind),
                "asset_id": asset.id,
            }
    return out


async def plan_scene(
    scene_id: uuid.UUID,
    *,
    skill: str,
    tier: str = "standard",
) -> SceneShots:
    with session_scope() as session:
        scene = session.get(Scene, scene_id)
        if scene is None:
            raise LookupError(f"scene {scene_id} not found")
        episode = session.get(Episode, scene.episode_id)
        label = f"EP{episode.number} SC{scene.number_label}"
        breakdown = scene.breakdown_json or {}
        heading = scene.heading_raw
        action = scene.action_text or ""
        dialogue = [
            f"{line.speaker_raw}"
            + (f" ({line.delivery_raw})" if line.delivery_raw else "")
            + f": {line.line}"
            for line in sorted(scene.dialogue, key=lambda d: d.order)
        ]
        present = [
            session.get(Character, link.character_id).name
            for link in session.scalars(
                select(SceneCharacter).where(SceneCharacter.scene_id == scene_id)
            )
            if session.get(Character, link.character_id)
        ]

    result = SceneShots(scene_id=scene_id, label=label)
    registry = get_registry()
    provider, model = registry.resolve(
        tier,
        TaskType.LLM_WRITER,
        log_context=LogContext(agent="shot-planner", target_type="scene", target_id=scene_id),
    )

    request = LLMRequest(
        task=TaskType.LLM_WRITER,
        system=skill,
        user=(
            f"SCENE: {label}\n{heading}\n\n"
            f"In frame: {', '.join(present) or 'unresolved'}\n"
            f"Props: {', '.join(breakdown.get('props', [])) or 'none'}\n\n"
            f"ACTION:\n{action}\n\n"
            f"DIALOGUE:\n" + "\n".join(dialogue) + "\n\n"
            "Write the spatial map once, then the shot list."
        ),
        json_schema=SHOT_LIST_SCHEMA,
        schema_name="shot_list",
        effort="low",
        max_tokens=24000,
    )

    try:
        response = await provider.complete(request, model)
    except Exception as exc:  # noqa: BLE001
        result.error = repr(exc)
        return result

    parsed = response.parsed or {}
    result.cost_usd = response.cost_usd or 0.0
    result.spatial_map = parsed.get("spatial_map", "")
    result.missing_assets = parsed.get("missing_assets", [])

    with session_scope() as session:
        for card in parsed.get("shots", []):
            letter = card.get("letter", "A")
            existing = session.scalar(
                select(Shot).where(Shot.scene_id == scene_id, Shot.letter == letter)
            )
            shot = existing or Shot(scene_id=scene_id, letter=letter)
            shot.duration_s = card.get("duration_s")
            shot.complexity = _complexity(card.get("complexity"))
            shot.shot_card_json = card
            shot.spatial_map = result.spatial_map
            shot.is_master_wide = bool(card.get("is_master_wide"))
            shot.order = len(result.shots)
            shot.status = ElementStatus.PLANNED
            if existing is None:
                session.add(shot)
            session.flush()
            result.shots.append(PlannedShot(letter=letter, card=card, shot_id=shot.id))

    return result


def _complexity(value: str | None) -> Complexity | None:
    try:
        return Complexity(value) if value else None
    except ValueError:
        return None


async def write_keyframe_prompt(
    shot_id: uuid.UUID,
    *,
    project_id: uuid.UUID,
    image_skill: str,
    tier: str = "standard",
) -> tuple[str, dict, list[str], float, str | None]:
    """Compose the keyframe prompt from approved assets plus the shot card."""
    with session_scope() as session:
        shot = session.get(Shot, shot_id)
        if shot is None:
            raise LookupError(f"shot {shot_id} not found")
        card = shot.shot_card_json or {}
        spatial_map = shot.spatial_map or ""
        scene = session.get(Scene, shot.scene_id)
        location = scene.location_raw if scene else None
        time_of_day = str(scene.time_of_day) if scene else "DAY"
        approved = _approved_assets(session, project_id)

    # Principle 3: a shot waits for the specific asset versions it references.
    wanted = list(card.get("characters", []))
    if location:
        wanted.append(location)

    references, missing = [], []
    for name in wanted:
        entry = approved.get((name or "").strip().lower())
        if entry:
            references.append(entry)
        else:
            missing.append(name)

    if missing:
        return "", {}, missing, 0.0, None

    registry = get_registry()
    provider, model = registry.resolve(
        tier,
        TaskType.LLM_WRITER,
        log_context=LogContext(
            agent="keyframe-prompt-writer", target_type="shot", target_id=shot_id
        ),
    )

    reference_block = "\n".join(
        f"  {r['tag']} ({r['variant']}): {r['descriptor']}" for r in references
    )
    request = LLMRequest(
        task=TaskType.LLM_WRITER,
        system=f"{_system(KEYFRAME_SYSTEM_SKILL)}\n\n--- IMAGE PROMPT SKILL ---\n\n{image_skill}",
        user=(
            f"SPATIAL MAP (paste-level truth for this scene):\n{spatial_map}\n\n"
            f"APPROVED ASSETS — use these descriptors verbatim:\n{reference_block}\n\n"
            f"Location: {location} ({time_of_day})\n\n"
            f"SHOT {card.get('letter')}\n"
            f"Goal: {card.get('goal')}\nAction: {card.get('action')}\n"
            f"Blocking: {card.get('blocking')}\nActing: {card.get('acting')}\n"
            f"Shot size: {card.get('shot_size')}  FOV: {card.get('lens_fov_deg')}°  "
            f"Angle: {card.get('camera_angle')}  Height: {card.get('camera_height')}\n"
        ),
        json_schema=KEYFRAME_SCHEMA,
        schema_name="keyframe_prompt",
        effort="low",
        max_tokens=16000,
    )

    try:
        response = await provider.complete(request, model)
    except Exception as exc:  # noqa: BLE001
        return "", {}, [], 0.0, repr(exc)

    parsed = response.parsed or {}
    sections = {k: v for k, v in parsed.items() if k != "full_prompt"}
    return parsed.get("full_prompt", ""), sections, [], response.cost_usd or 0.0, None


async def generate_keyframe(
    shot_id: uuid.UUID,
    *,
    project_id: uuid.UUID,
    image_skill: str,
    skill_versions: dict,
    aspect: str = "9:16",
    candidates: int = 2,
    tier: str = "standard",
) -> tuple[GenerationBatch | None, list[str], str | None]:
    prompt, sections, missing, _, error = await write_keyframe_prompt(
        shot_id, project_id=project_id, image_skill=image_skill, tier=tier
    )
    if error:
        return None, [], error
    if missing:
        # Not a failure — the shot is waiting on a gate, which is the only thing
        # allowed to block (principle 3).
        return None, missing, None
    if not prompt:
        return None, [], "keyframe prompt was empty"

    with session_scope() as session:
        shot = session.get(Shot, shot_id)
        asset = session.scalar(
            select(Asset).where(
                Asset.project_id == project_id,
                Asset.entity_type == "keyframe",
                Asset.entity_id == shot_id,
            )
        )
        if asset is None:
            scene = session.get(Scene, shot.scene_id)
            episode = session.get(Episode, scene.episode_id)
            asset = Asset(
                project_id=project_id,
                entity_type="keyframe",
                entity_id=shot_id,
                kind=AssetKind.VIEW,
                variant_label=f"keyframe {shot.letter}",
                tag=f"@key_ep{episode.number}_sc{scene.number_label}{shot.letter}",
                descriptor_text=f"EP{episode.number} SC{scene.number_label}{shot.letter}",
                aspect=aspect,
                status=ElementStatus.PLANNED,
            )
            session.add(asset)
            session.flush()
        asset_id = asset.id
        shot.status = ElementStatus.GENERATING

    batch = await generate_candidates(
        session_scope,
        asset_id=asset_id,
        project_id=project_id,
        prompt=prompt,
        prompt_sections=sections,
        aspect=aspect,
        count=candidates,
        skill_versions=skill_versions,
    )

    # Pin exactly which asset versions this shot consumed.
    with session_scope() as session:
        shot = session.get(Shot, shot_id)
        shot.status = (
            ElementStatus.AGENT_REVIEW if batch.succeeded else ElementStatus.ESCALATED
        )

    return batch, [], None


async def plan_project_shots(
    project_id: uuid.UUID,
    *,
    limit: int | None = None,
    concurrency: int = 3,
    tier: str = "standard",
) -> list[SceneShots]:
    """Plan shots for a project's scenes. Planning is cheap; keyframes are not."""
    from vide.registry import load

    with session_scope() as session:
        skill, _ = load(session, ["shot-planning"])
        scene_ids = [
            s.id
            for s in session.scalars(
                select(Scene)
                .join(Episode, Scene.episode_id == Episode.id)
                .where(Episode.project_id == project_id)
                .order_by(Episode.number, Scene.number)
            )
        ]
    if limit:
        scene_ids = scene_ids[:limit]

    log.info("shots.planning", project=str(project_id), scenes=len(scene_ids))
    semaphore = asyncio.Semaphore(concurrency)

    async def one(scene_id: uuid.UUID) -> SceneShots:
        async with semaphore:
            return await plan_scene(scene_id, skill=skill, tier=tier)

    results = await asyncio.gather(*(one(s) for s in scene_ids), return_exceptions=True)
    out: list[SceneShots] = []
    for scene_id, outcome in zip(scene_ids, results, strict=True):
        if isinstance(outcome, BaseException):
            out.append(SceneShots(scene_id=scene_id, label="?", error=repr(outcome)))
        else:
            out.append(outcome)
    return out


__all__ = [
    "PlannedShot",
    "SceneShots",
    "ShotAssetRef",
    "generate_keyframe",
    "plan_project_shots",
    "plan_scene",
    "write_keyframe_prompt",
]

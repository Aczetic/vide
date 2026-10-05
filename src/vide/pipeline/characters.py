"""a later stage orchestration: specs → prompts → sheets.

Runs the character pipeline for a project. Each character is independent, so
they fan out — a character that clears its gate moves on without waiting for
the rest (principle 2).

Order matters and is not negotiable: a master sheet is generated before any
state variant, because a variant is an edit of the approved master rather than
a fresh generation. Generating states in parallel with the master would
produce four unrelated people.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field

import structlog
from sqlalchemy import select

from vide.db import session_scope
from vide.models.asset import Asset
from vide.models.core import Episode, Scene, SceneCharacter
from vide.models.entity import Character
from vide.models.enums import AssetKind, ElementStatus
from vide.pipeline.generate import GenerationBatch, generate_candidates
from vide.pipeline.prompts import SheetPrompt, write_sheet_prompt
from vide.pipeline.specs import CharacterSpec, write_spec
from vide.registry import load

log = structlog.get_logger(__name__)


@dataclass(slots=True)
class CharacterRun:
    character_id: uuid.UUID
    name: str
    spec: CharacterSpec | None = None
    prompt: SheetPrompt | None = None
    batch: GenerationBatch | None = None
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors and self.batch is not None and bool(self.batch.succeeded)


def _gather_context(session, character_id: uuid.UUID) -> list[dict]:
    """Every scene this character appears in, with what the text says there."""
    rows = session.execute(
        select(Scene, Episode.number)
        .join(SceneCharacter, SceneCharacter.scene_id == Scene.id)
        .join(Episode, Scene.episode_id == Episode.id)
        .where(SceneCharacter.character_id == character_id)
        .order_by(Episode.number, Scene.number)
    ).all()

    out = []
    for scene, episode_number in rows:
        breakdown = scene.breakdown_json or {}
        out.append(
            {
                "uid": f"{episode_number}:{scene.number_label}",
                "time_of_day": str(scene.time_of_day),
                "location_raw": scene.location_raw,
                "story_day": scene.story_day,
                "extracted": breakdown,
            }
        )
    return out


async def run_character(
    character_id: uuid.UUID,
    *,
    project_id: uuid.UUID,
    acting_skill: str,
    image_skill: str,
    skill_versions: dict,
    candidates: int = 4,
    generate_images: bool = True,
) -> CharacterRun:
    with session_scope() as session:
        character = session.get(Character, character_id)
        if character is None:
            raise LookupError(f"character {character_id} not found")
        name = character.name
        tier = str(character.tier) if character.tier else None
        line_count = character.line_count
        scenes = _gather_context(session, character_id)

    run = CharacterRun(character_id=character_id, name=name)

    # 1. Spec
    spec = await write_spec(
        character_id=str(character_id),
        name=name,
        tier=tier,
        scenes=scenes,
        line_count=line_count,
        acting_skill=acting_skill,
    )
    run.spec = spec
    if not spec.ok:
        run.errors.append(f"spec: {spec.error}")
        return run

    with session_scope() as session:
        row = session.get(Character, character_id)
        row.spec_json = {**(row.spec_json or {}), **spec.spec}
        row.acting_profile = spec.spec.get("acting_profile")
        row.voice_block = spec.spec.get("voice_block")
        row.gaps = spec.spec.get("gaps", [])
        row.status = ElementStatus.PROMPTING

    # 2. Sheet prompt
    prompt = await write_sheet_prompt(
        character_id=str(character_id),
        name=name,
        spec=spec.spec,
        image_skill=image_skill,
    )
    run.prompt = prompt
    if prompt.error:
        run.errors.append(f"prompt: {prompt.error}")
        return run
    if prompt.violations:
        # A prompt that breaks a named rule is not sent. Each rule exists
        # because a generation failed without it, so spending four candidates
        # to rediscover that is waste.
        run.errors.append(f"prompt violated rules: {'; '.join(prompt.violations)}")
        with session_scope() as session:
            session.get(Character, character_id).status = ElementStatus.ESCALATED
        return run

    # 3. The master-sheet asset this generation belongs to
    with session_scope() as session:
        asset = session.scalar(
            select(Asset).where(
                Asset.project_id == project_id,
                Asset.entity_id == character_id,
                Asset.kind == AssetKind.MASTER,
            )
        )
        if asset is None:
            asset = Asset(
                project_id=project_id,
                entity_type="character",
                entity_id=character_id,
                kind=AssetKind.MASTER,
                variant_label="master sheet",
                tag=f"@char_{name.lower().replace(' ', '')[:20]}_master",
                descriptor_text=name,
                aspect="16:9",
                status=ElementStatus.PLANNED,
            )
            session.add(asset)
            session.flush()
        asset.descriptor_text = name
        asset_id = asset.id

    if not generate_images:
        return run

    # 4. N candidates
    run.batch = await generate_candidates(
        session_scope,
        asset_id=asset_id,
        project_id=project_id,
        prompt=prompt.prompt,
        prompt_sections=prompt.sections,
        aspect="16:9",
        count=candidates,
        skill_versions=skill_versions,
    )
    if not run.batch.succeeded:
        run.errors.append("every candidate failed to generate")
    return run


async def run_project_characters(
    project_id: uuid.UUID,
    *,
    tiers: tuple[str, ...] = ("lead", "supporting"),
    limit: int | None = None,
    candidates: int = 4,
    # Each character fans out into N image calls, so even 2 characters at once
    # can exceed the provider's in-flight budget.
    concurrency: int = 2,
    generate_images: bool = True,
) -> list[CharacterRun]:
    """Run the character pipeline, leads first.

    Leads first because they carry the most scenes, so a mistake in a lead's
    face propagates furthest and is worth catching before anything else is built.
    """
    with session_scope() as session:
        skills_text, skill_versions = load(session, ["acting", "image-prompt"])
        acting_skill, image_skill = skills_text.split("\n\n---\n\n", 1)

        query = select(Character).where(Character.project_id == project_id)
        if tiers:
            query = query.where(Character.tier.in_(tiers))
        characters = list(
            session.scalars(query.order_by(Character.line_count.desc()))
        )
        if limit:
            characters = characters[:limit]
        ids = [(c.id, c.name) for c in characters]

    log.info("characters.start", project=str(project_id), count=len(ids))
    semaphore = asyncio.Semaphore(concurrency)

    async def one(character_id: uuid.UUID) -> CharacterRun:
        async with semaphore:
            return await run_character(
                character_id,
                project_id=project_id,
                acting_skill=acting_skill,
                image_skill=image_skill,
                skill_versions=skill_versions,
                candidates=candidates,
                generate_images=generate_images,
            )

    runs = await asyncio.gather(
        *(one(cid) for cid, _ in ids), return_exceptions=True
    )

    out: list[CharacterRun] = []
    for (character_id, name), result in zip(ids, runs, strict=True):
        if isinstance(result, BaseException):
            out.append(
                CharacterRun(character_id=character_id, name=name, errors=[repr(result)])
            )
        else:
            out.append(result)
    return out

"""a later stage orchestration: location plates and prop sheets.

Order is forced, not incidental. The **geography master** is generated and must
exist before any coverage plate of the same set, because every coverage plate
inherits the room from it. Generating them together produces a set of unrelated
rooms that happen to share a name.

Heaviest-reuse sets go first. A mistake in the set that carries eleven scenes
propagates further than one in a set that carries a single scene.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field

import structlog
from sqlalchemy import select

from vide.db import session_scope
from vide.models.asset import Asset
from vide.models.core import Episode, Project, Scene
from vide.models.entity import LocationSubArea, Prop
from vide.models.enums import AssetKind, ElementStatus, PlateType
from vide.pipeline.generate import GenerationBatch, generate_candidates
from vide.pipeline.places import (
    PlatePrompt,
    write_location_spec,
    write_plate_prompt,
    write_prop_prompt,
)
from vide.registry import load

log = structlog.get_logger(__name__)


@dataclass(slots=True)
class PlaceRun:
    entity_id: uuid.UUID
    name: str
    kind: str
    prompts: list[PlatePrompt] = field(default_factory=list)
    batches: list[GenerationBatch] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def images(self) -> int:
        return sum(len(b.succeeded) for b in self.batches)

    @property
    def ok(self) -> bool:
        return not self.errors and self.images > 0


def _scene_context(session, *, sub_area_name: str | None, prop_name: str | None,
                   project_id: uuid.UUID) -> list[dict]:
    """Scenes that use this set or prop, with what the breakdown said."""
    rows = session.execute(
        select(Scene, Episode.number)
        .join(Episode, Scene.episode_id == Episode.id)
        .where(Episode.project_id == project_id)
        .order_by(Episode.number, Scene.number)
    ).all()

    out = []
    for scene, episode_number in rows:
        breakdown = scene.breakdown_json or {}
        if sub_area_name:
            haystack = " ".join(
                filter(None, [scene.location_raw, scene.sub_area_raw])
            ).lower()
            implied = " ".join(
                entry.get("name_raw", "")
                for entry in breakdown.get("implied_locations", [])
            ).lower()
            if sub_area_name.lower() not in haystack and sub_area_name.lower() not in implied:
                continue
        if prop_name:
            props = " ".join(breakdown.get("props", []) + breakdown.get("screens_inserts", []))
            if prop_name.lower() not in props.lower():
                continue
        out.append(
            {
                "uid": f"{episode_number}:{scene.number_label}",
                "time_of_day": str(scene.time_of_day),
                "extracted": breakdown,
            }
        )
    return out


def _upsert_asset(
    session,
    *,
    project_id: uuid.UUID,
    entity_id: uuid.UUID,
    entity_type: str,
    kind: AssetKind,
    variant_label: str,
    plate_type: PlateType | None,
    name: str,
    aspect: str,
) -> uuid.UUID:
    asset = session.scalar(
        select(Asset).where(
            Asset.project_id == project_id,
            Asset.entity_id == entity_id,
            Asset.variant_label == variant_label,
        )
    )
    if asset is None:
        slug = "".join(c for c in name.lower() if c.isalnum())[:22]
        asset = Asset(
            project_id=project_id,
            entity_type=entity_type,
            entity_id=entity_id,
            kind=kind,
            plate_type=plate_type,
            variant_label=variant_label,
            tag=f"@{entity_type[:4]}_{slug}_{variant_label.split()[0][:8]}",
            descriptor_text=name,
            aspect=aspect,
            status=ElementStatus.PLANNED,
        )
        session.add(asset)
        session.flush()
    asset.descriptor_text = name
    return asset.id


async def run_sub_area(
    sub_area_id: uuid.UUID,
    *,
    project_id: uuid.UUID,
    location_skill: str,
    image_skill: str,
    skill_versions: dict,
    delivery_aspect: str = "9:16",
    era: str | None = None,
    candidates: int = 4,
    coverage_times: int = 1,
) -> PlaceRun:
    with session_scope() as session:
        area = session.get(LocationSubArea, sub_area_id)
        if area is None:
            raise LookupError(f"sub-area {sub_area_id} not found")
        name = area.name
        scenes = _scene_context(
            session, sub_area_name=name, prop_name=None, project_id=project_id
        )

    run = PlaceRun(entity_id=sub_area_id, name=name, kind="location")

    spec = await write_location_spec(
        entity_id=str(sub_area_id), name=name, scenes=scenes,
        skill=location_skill, era=era,
    )
    if not spec.ok:
        run.errors.append(f"spec: {spec.error}")
        return run

    with session_scope() as session:
        row = session.get(LocationSubArea, sub_area_id)
        row.spec_json = {**(row.spec_json or {}), **spec.spec}
        row.continuity_risk = spec.spec.get("continuity_risk")
        row.gaps = spec.spec.get("gaps", [])
        row.times_of_day = spec.spec.get("times_of_day", [])
        row.status = ElementStatus.PROMPTING

    times = spec.spec.get("times_of_day") or ["day"]

    # 1. Geography master — 16:9, the whole room, never a first frame. Must
    #    exist before any coverage plate, which inherits the room from it.
    master = await write_plate_prompt(
        entity_id=str(sub_area_id), name=name, spec=spec.spec,
        image_skill=image_skill, time_of_day=times[0], is_master=True, era=era,
    )
    run.prompts.append(master)
    if not master.ok:
        run.errors.append(f"master prompt: {master.error or master.violations}")
        return run

    with session_scope() as session:
        master_asset_id = _upsert_asset(
            session, project_id=project_id, entity_id=sub_area_id,
            entity_type="location", kind=AssetKind.MASTER,
            variant_label="geography master",
            plate_type=PlateType.GEOGRAPHY_MASTER, name=name, aspect="16:9",
        )

    run.batches.append(
        await generate_candidates(
            session_scope, asset_id=master_asset_id, project_id=project_id,
            prompt=master.prompt, prompt_sections=master.sections,
            aspect="16:9", count=candidates, skill_versions=skill_versions,
        )
    )
    if not run.batches[-1].succeeded:
        run.errors.append("geography master produced no images")
        return run

    # 2. Coverage plates in the delivery ratio, one per time of day.
    for time_of_day in times[:coverage_times]:
        plate = await write_plate_prompt(
            entity_id=str(sub_area_id), name=name, spec=spec.spec,
            image_skill=image_skill, time_of_day=time_of_day,
            is_master=False, era=era,
        )
        run.prompts.append(plate)
        if not plate.ok:
            run.errors.append(f"coverage prompt ({time_of_day}): {plate.error or plate.violations}")
            continue

        with session_scope() as session:
            coverage_id = _upsert_asset(
                session, project_id=project_id, entity_id=sub_area_id,
                entity_type="location", kind=AssetKind.PLATE,
                variant_label=f"coverage plate — {time_of_day.lower()}",
                plate_type=PlateType.COVERAGE_PLATE, name=name,
                aspect=delivery_aspect,
            )
        run.batches.append(
            await generate_candidates(
                session_scope, asset_id=coverage_id, project_id=project_id,
                prompt=plate.prompt, prompt_sections=plate.sections,
                aspect=delivery_aspect, count=candidates,
                skill_versions=skill_versions,
            )
        )
    return run


async def run_prop(
    prop_id: uuid.UUID,
    *,
    project_id: uuid.UUID,
    image_skill: str,
    skill_versions: dict,
    candidates: int = 4,
) -> PlaceRun:
    with session_scope() as session:
        prop = session.get(Prop, prop_id)
        if prop is None:
            raise LookupError(f"prop {prop_id} not found")
        name = prop.name
        kind = str(prop.kind)
        scenes = _scene_context(
            session, sub_area_name=None, prop_name=name, project_id=project_id
        )

    run = PlaceRun(entity_id=prop_id, name=name, kind=kind)

    prompt = await write_prop_prompt(
        entity_id=str(prop_id), name=name, scenes=scenes, image_skill=image_skill
    )
    run.prompts.append(prompt)
    if not prompt.ok:
        run.errors.append(f"prompt: {prompt.error or prompt.violations}")
        return run

    with session_scope() as session:
        asset_id = _upsert_asset(
            session, project_id=project_id, entity_id=prop_id,
            entity_type=kind, kind=AssetKind.SHEET,
            variant_label="prop sheet — default", plate_type=None,
            name=name, aspect="1:1",
        )
    run.batches.append(
        await generate_candidates(
            session_scope, asset_id=asset_id, project_id=project_id,
            prompt=prompt.prompt, prompt_sections=prompt.sections,
            aspect="1:1", count=candidates, skill_versions=skill_versions,
        )
    )
    return run


async def run_project_places(
    project_id: uuid.UUID,
    *,
    locations: int | None = 6,
    props: int | None = 4,
    candidates: int = 4,
    concurrency: int = 2,
) -> list[PlaceRun]:
    """Build location plates and prop sheets, heaviest reuse first."""
    with session_scope() as session:
        project = session.get(Project, project_id)
        era = project.era if project else None
        delivery = project.aspect_delivery if project else "9:16"

        skills_text, skill_versions = load(session, ["location-spec", "image-prompt"])
        location_skill, image_skill = skills_text.split("\n\n---\n\n", 1)

        area_ids = [
            (a.id, a.name)
            for a in session.scalars(
                select(LocationSubArea)
                .where(LocationSubArea.project_id == project_id)
                .order_by(LocationSubArea.scene_count.desc())
            )
        ][: locations or None]
        prop_ids = [
            (p.id, p.name)
            for p in session.scalars(
                select(Prop).where(Prop.project_id == project_id)
                .order_by(Prop.scene_count.desc())
            )
        ][: props or None]

    log.info(
        "places.start", project=str(project_id),
        locations=len(area_ids), props=len(prop_ids),
    )
    semaphore = asyncio.Semaphore(concurrency)

    async def one_area(area_id: uuid.UUID) -> PlaceRun:
        async with semaphore:
            return await run_sub_area(
                area_id, project_id=project_id, location_skill=location_skill,
                image_skill=image_skill, skill_versions=skill_versions,
                delivery_aspect=delivery, era=era, candidates=candidates,
            )

    async def one_prop(prop_id: uuid.UUID) -> PlaceRun:
        async with semaphore:
            return await run_prop(
                prop_id, project_id=project_id, image_skill=image_skill,
                skill_versions=skill_versions, candidates=candidates,
            )

    outcomes = await asyncio.gather(
        *[one_area(i) for i, _ in area_ids],
        *[one_prop(i) for i, _ in prop_ids],
        return_exceptions=True,
    )

    runs: list[PlaceRun] = []
    for (entity_id, name), outcome in zip(area_ids + prop_ids, outcomes, strict=True):
        if isinstance(outcome, BaseException):
            runs.append(
                PlaceRun(entity_id=entity_id, name=name, kind="?", errors=[repr(outcome)])
            )
        else:
            runs.append(outcome)
    return runs

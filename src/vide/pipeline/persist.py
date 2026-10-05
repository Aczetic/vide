"""Write a later stage output into the schema.

Until now the pipeline produced JSON files. That is fine for one script on one
laptop and wrong for the product: five people reviewing five titles need shared
state, and the UI needs to query it rather than parse a directory.

Re-running a project replaces its derived rows rather than appending. The source
documents and the project itself survive, so re-running after a skill change
updates the analysis without losing the project.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

import structlog
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from vide.models.asset import Asset, ContinuityEntry
from vide.models.core import (
    DialogueLine,
    Episode,
    Organization,
    Project,
    Scene,
    SceneCharacter,
    SceneMention,
    SourceDocument,
)
from vide.models.entity import (
    Character,
    Location,
    LocationSubArea,
    MergeQuestion,
    Prop,
)
from vide.models.enums import (
    AssetKind,
    CharacterTier,
    ElementStatus,
    IntExt,
    PlateType,
    ProjectStatus,
    PropKind,
    SourceKind,
    TimeOfDay,
)

log = structlog.get_logger(__name__)


@dataclass(slots=True)
class PersistResult:
    project_id: uuid.UUID
    created: dict[str, int]


def _enum_or_none(enum_cls, value: str | None):
    if not value:
        return None
    try:
        return enum_cls(value)
    except ValueError:
        return None


def get_or_create_org(session: Session, name: str = "Default") -> Organization:
    org = session.scalar(select(Organization).where(Organization.slug == "default"))
    if org is None:
        org = Organization(name=name, slug="default")
        session.add(org)
        session.flush()
    return org


def _clear_derived(session: Session, project_id: uuid.UUID) -> None:
    """Remove everything the pipeline derives, leaving the project and its sources."""
    episode_ids = list(
        session.scalars(select(Episode.id).where(Episode.project_id == project_id))
    )
    if episode_ids:
        scene_ids = list(
            session.scalars(select(Scene.id).where(Scene.episode_id.in_(episode_ids)))
        )
        if scene_ids:
            for model in (DialogueLine, SceneMention, SceneCharacter):
                session.execute(delete(model).where(model.scene_id.in_(scene_ids)))
        session.execute(delete(Scene).where(Scene.episode_id.in_(episode_ids)))
    session.execute(delete(Episode).where(Episode.project_id == project_id))

    for model in (
        Asset, ContinuityEntry, MergeQuestion, Character, Prop, LocationSubArea, Location,
    ):
        session.execute(delete(model).where(model.project_id == project_id))
    session.flush()


def persist_run(
    session: Session,
    *,
    name: str,
    code: str,
    screenplay: Any,
    breakdowns: list[dict[str, Any]],
    entities: Any,
    story_days: Any,
    design: Any,
    source_filename: str,
    source_text: str,
    client_name: str | None = None,
) -> PersistResult:
    org = get_or_create_org(session)

    project = session.scalar(
        select(Project).where(Project.organization_id == org.id, Project.code == code)
    )
    if project is None:
        project = Project(
            organization_id=org.id,
            name=name,
            code=code,
            client_name=client_name,
            status=ProjectStatus.ACTIVE,
            episodes_target=screenplay.stats["episodes"],
        )
        session.add(project)
        session.flush()
    else:
        _clear_derived(session, project.id)

    counts: dict[str, int] = {}

    # -- source document ---------------------------------------------------
    existing_source = session.scalar(
        select(SourceDocument).where(
            SourceDocument.project_id == project.id,
            SourceDocument.filename == source_filename,
        )
    )
    if existing_source is None:
        session.add(
            SourceDocument(
                project_id=project.id,
                kind=SourceKind.SCREENPLAY,
                filename=source_filename,
                text=source_text,
            )
        )
        session.flush()

    # -- episodes and scenes ------------------------------------------------
    scene_by_uid: dict[str, Scene] = {}
    breakdown_by_uid = {row["uid"]: row for row in breakdowns}

    for parsed_episode in screenplay.episodes:
        episode = Episode(
            project_id=project.id,
            number=parsed_episode.number,
            label=f"EP{parsed_episode.number:02d}",
        )
        session.add(episode)
        session.flush()

        for parsed_scene in parsed_episode.scenes:
            row = breakdown_by_uid.get(parsed_scene.uid, {})
            extracted = row.get("extracted", {})
            scene = Scene(
                episode_id=episode.id,
                number=parsed_scene.number,
                number_label=parsed_scene.number_label,
                intercut_group=parsed_scene.intercut_group,
                int_ext=_enum_or_none(IntExt, parsed_scene.int_ext),
                time_of_day=_enum_or_none(TimeOfDay, parsed_scene.time_of_day)
                or TimeOfDay.UNSPECIFIED,
                story_day=(
                    f"D{story_days.scene_to_day[parsed_scene.uid]}"
                    if parsed_scene.uid in story_days.scene_to_day
                    else None
                ),
                story_day_hint=extracted.get("story_day_hint"),
                location_raw=parsed_scene.location_raw,
                sub_area_raw=parsed_scene.sub_area_raw,
                heading_raw=parsed_scene.heading_raw,
                action_text=parsed_scene.action_text,
                summary=extracted.get("action_summary"),
                emotional_beat=extracted.get("emotional_beat"),
                transition_out=parsed_scene.transition_out,
                source_span={
                    "start_line": parsed_scene.start_line,
                    "end_line": parsed_scene.end_line,
                    "uid": parsed_scene.uid,
                },
                breakdown_json=extracted,
                gaps=parsed_scene.gaps + extracted.get("gaps", []),
                status=ElementStatus.PLANNED,
            )
            session.add(scene)
            session.flush()
            scene_by_uid[parsed_scene.uid] = scene

            for line in parsed_scene.dialogue:
                session.add(
                    DialogueLine(
                        scene_id=scene.id,
                        order=line.order,
                        speaker_raw=line.speaker_raw,
                        line=line.line,
                        delivery_raw=line.delivery_raw,
                        delivery_action=line.delivery_action,
                        delivery_emotion=line.delivery_emotion,
                        source_span={"line": line.line_no},
                    )
                )

    counts["episodes"] = len(screenplay.episodes)
    counts["scenes"] = len(scene_by_uid)
    session.flush()

    # -- entities -----------------------------------------------------------
    character_by_name: dict[str, Character] = {}
    for entity in entities.of("character"):
        character = Character(
            project_id=project.id,
            name=entity.canonical_name,
            tier=_enum_or_none(CharacterTier, entity.tier),
            scene_count=entity.scene_count,
            line_count=entity.line_count,
            speaks=entity.line_count > 0,
            spec_json={"surfaces": entity.surfaces, "confidence": entity.confidence},
            gaps=[entity.note] if entity.note else [],
            status=ElementStatus.PLANNED,
        )
        session.add(character)
        character_by_name[entity.canonical_name] = character
    session.flush()
    counts["characters"] = len(character_by_name)

    location_by_name: dict[str, Location] = {}
    for entity in entities.of("location"):
        location = Location(project_id=project.id, name=entity.canonical_name)
        session.add(location)
        location_by_name[entity.canonical_name] = location
    session.flush()

    # Sub-areas need a parent. Where the script never named one, attach them to
    # a synthetic "Unassigned" location rather than dropping them.
    fallback: Location | None = None
    for entity in entities.of("sub_area"):
        target = entity.canonical_name.lower()
        parent = next(
            (loc for name, loc in location_by_name.items() if name.lower() in target),
            None,
        )
        if parent is None:
            if fallback is None:
                fallback = Location(project_id=project.id, name="Unassigned")
                session.add(fallback)
                session.flush()
            parent = fallback
        session.add(
            LocationSubArea(
                location_id=parent.id,
                project_id=project.id,
                name=entity.canonical_name,
                scene_count=entity.scene_count,
                spec_json={"surfaces": entity.surfaces, "confidence": entity.confidence},
                status=ElementStatus.PLANNED,
            )
        )
    session.flush()
    counts["locations"] = len(location_by_name)
    counts["sub_areas"] = len(entities.of("sub_area"))

    for kind, prop_kind in (
        ("prop", PropKind.PROP),
        ("vehicle", PropKind.VEHICLE),
        ("screen_insert", PropKind.SCREEN_INSERT),
    ):
        for entity in entities.of(kind):
            session.add(
                Prop(
                    project_id=project.id,
                    name=entity.canonical_name,
                    kind=prop_kind,
                    scene_count=entity.scene_count,
                    spec_json={"surfaces": entity.surfaces},
                    status=ElementStatus.PLANNED,
                )
            )
    session.flush()
    counts["props"] = sum(
        len(entities.of(k)) for k in ("prop", "vehicle", "screen_insert")
    )

    # -- scene ↔ character links -------------------------------------------
    surface_to_character = {
        surface.lower(): entity.canonical_name
        for entity in entities.of("character")
        for surface in entity.surfaces
    }
    links = 0
    for uid, scene in scene_by_uid.items():
        row = breakdown_by_uid.get(uid, {})
        present: dict[str, bool] = {}
        for entry in row.get("extracted", {}).get("characters_present", []):
            name = surface_to_character.get(entry.get("name_raw", "").lower())
            if name:
                present[name] = present.get(name, False) or bool(entry.get("speaks"))
        for raw in row.get("heading_cast", []):
            name = surface_to_character.get(raw.lower())
            if name:
                present.setdefault(name, False)

        for name, speaks in present.items():
            character = character_by_name.get(name)
            if character is None:
                continue
            session.add(
                SceneCharacter(
                    scene_id=scene.id,
                    character_id=character.id,
                    speaks=speaks,
                )
            )
            session.add(
                SceneMention(
                    scene_id=scene.id,
                    entity_type="character",
                    raw_text=name,
                    speaks=speaks,
                    resolved_entity_id=character.id,
                    confidence=1.0,
                )
            )
            links += 1
    counts["scene_character_links"] = links

    # -- merge questions (G1) ----------------------------------------------
    for question in entities.questions:
        session.add(
            MergeQuestion(
                project_id=project.id,
                entity_type=question.entity_type,
                question=question.question,
                candidates_json=question.candidates,
                recommendation=question.recommendation,
            )
        )
    counts["questions"] = len(entities.questions)

    # -- continuity matrix --------------------------------------------------
    for cell in design.continuity:
        character = character_by_name.get(cell.character)
        if character is None:
            continue
        session.add(
            ContinuityEntry(
                project_id=project.id,
                story_day=f"D{cell.story_day}",
                character_id=character.id,
                costume_label=cell.variant_label,
                state_notes="; ".join(cell.state_cues) or None,
                episodes=cell.episodes,
            )
        )
    counts["continuity_cells"] = len(design.continuity)

    # -- the design order, as planned assets --------------------------------
    # The build list and the production tracker are the same object: an Asset
    # row with status `planned` is a thing that has to be made.
    # Resolve each build back to the entity it belongs to, so the UI can group
    # by character and a later stage can attach a spec. A random id would orphan every
    # build from the thing it is a build of.
    sub_area_by_name = {
        area.name: area
        for area in session.scalars(
            select(LocationSubArea).where(LocationSubArea.project_id == project.id)
        )
    }
    prop_by_name = {
        prop.name: prop
        for prop in session.scalars(select(Prop).where(Prop.project_id == project.id))
    }

    for index, item in enumerate(design.items):
        owner = (
            character_by_name.get(item.entity_name)
            or sub_area_by_name.get(item.entity_name)
            or location_by_name.get(item.entity_name)
            or prop_by_name.get(item.entity_name)
        )
        slug = "".join(c for c in item.entity_name.lower() if c.isalnum())[:24]
        session.add(
            Asset(
                project_id=project.id,
                entity_type=item.entity_type,
                entity_id=owner.id if owner is not None else uuid.uuid4(),
                kind=(
                    AssetKind.MASTER
                    if item.variant_label in ("master sheet", "geography master")
                    else AssetKind.VARIANT
                ),
                plate_type=_enum_or_none(PlateType, item.plate_type),
                variant_label=item.variant_label,
                scenes_mark=item.scenes,
                tag=f"@{item.entity_type[:4]}_{project.code}_{slug}_{index}",
                # The display name. Until specs exist later this is what the UI
                # shows, so it must survive rather than live only inside the tag.
                descriptor_text=item.entity_name,
                reason=item.reason,
                gaps=item.gaps,
                status=ElementStatus.PLANNED,
            )
        )
    counts["planned_assets"] = len(design.items)

    session.flush()
    log.info("persist.complete", project=str(project.id), **counts)
    return PersistResult(project_id=project.id, created=counts)

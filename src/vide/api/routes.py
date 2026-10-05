"""API routes.

Scoped to what the Watchlist, Plot and Story tabs need. Generation-triggering
endpoints are deliberately absent — they arrive with a later stage, and when they do they
carry the role check that requires.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from vide.api.schemas import (
    AssetOut,
    BuildItemOut,
    CandidateOut,
    DialogueOut,
    EntityOut,
    EpisodeSummary,
    GapOut,
    GateDecisionIn,
    GateDecisionOut,
    ProjectDetail,
    ProjectTile,
    QuestionOut,
    QueueItem,
    ReportsOut,
    SceneDetail,
    SceneShotsOut,
    SceneSummary,
    ShotOut,
    SkillOut,
)
from vide.db import get_session
from vide.models.asset import Asset, Shot
from vide.models.core import (
    DialogueLine,
    Episode,
    Project,
    Scene,
    SceneCharacter,
    SourceDocument,
)
from vide.models.entity import Character, LocationSubArea, MergeQuestion, Prop
from vide.models.enums import (
    ActorKind,
    ElementStatus,
    Gate,
    GateDecisionKind,
    GenerationStatus,
    ReviewVerdict,
)
from vide.models.generation import GateDecision, Generation, Review
from vide.models.registry import Skill
from vide.storage import get_client, get_layout

router = APIRouter()


def _progress(session: Session, project_id: uuid.UUID) -> dict[str, dict[str, int]]:
    rows = session.execute(
        select(Asset.entity_type, Asset.status, func.count())
        .where(Asset.project_id == project_id)
        .group_by(Asset.entity_type, Asset.status)
    )
    out: dict[str, dict[str, int]] = {}
    for entity_type, status, count in rows:
        bucket = out.setdefault(entity_type, {"planned": 0, "approved": 0, "total": 0})
        bucket["total"] += count
        if status == ElementStatus.APPROVED:
            bucket["approved"] += count
        else:
            bucket["planned"] += count
    return out


@router.get("/projects", response_model=list[ProjectTile])
def list_projects(session: Session = Depends(get_session)) -> list[ProjectTile]:
    """Watchlist. Clients would be filtered to their own projects here —
    the filter lands with auth later's end."""
    tiles = []
    for project in session.scalars(select(Project).order_by(Project.created_at.desc())):
        episodes = session.scalar(
            select(func.count()).select_from(Episode).where(Episode.project_id == project.id)
        ) or 0
        scenes = session.scalar(
            select(func.count())
            .select_from(Scene)
            .join(Episode)
            .where(Episode.project_id == project.id)
        ) or 0
        awaiting = session.scalar(
            select(func.count())
            .select_from(MergeQuestion)
            .where(MergeQuestion.project_id == project.id, ~MergeQuestion.resolved)
        ) or 0
        tiles.append(
            ProjectTile(
                id=project.id,
                name=project.name,
                code=project.code,
                client_name=project.client_name,
                format=str(project.format),
                status=str(project.status),
                episodes=episodes,
                scenes=scenes,
                cover_image_url=project.cover_image_url,
                progress=_progress(session, project.id),
                awaiting_decision=awaiting,
                last_activity=project.updated_at.isoformat() if project.updated_at else None,
            )
        )
    return tiles


def _get_project(session: Session, project_id: uuid.UUID) -> Project:
    project = session.get(Project, project_id)
    if project is None:
        raise HTTPException(404, "project not found")
    return project


@router.get("/projects/{project_id}", response_model=ProjectDetail)
def get_project(
    project_id: uuid.UUID, session: Session = Depends(get_session)
) -> ProjectDetail:
    project = _get_project(session, project_id)
    sources = [
        {
            "id": str(doc.id),
            "kind": str(doc.kind),
            "filename": doc.filename,
            "chars": len(doc.text or ""),
        }
        for doc in session.scalars(
            select(SourceDocument).where(SourceDocument.project_id == project_id)
        )
    ]

    def count(model, **where):
        stmt = select(func.count()).select_from(model).where(model.project_id == project_id)
        return session.scalar(stmt) or 0

    scenes = session.scalar(
        select(func.count()).select_from(Scene).join(Episode)
        .where(Episode.project_id == project_id)
    ) or 0

    return ProjectDetail(
        id=project.id,
        name=project.name,
        code=project.code,
        client_name=project.client_name,
        format=str(project.format),
        aspect_delivery=project.aspect_delivery,
        episodes_target=project.episodes_target,
        runtime_target_s=project.runtime_target_s,
        era=project.era,
        model_tier=project.model_tier,
        candidates_per_asset=project.candidates_per_asset,
        attempt_budget=project.attempt_budget,
        status=str(project.status),
        sources=sources,
        counts={
            "episodes": count(Episode),
            "scenes": scenes,
            "characters": count(Character),
            "sub_areas": count(LocationSubArea),
            "props": count(Prop),
            "builds": count(Asset),
            "questions": count(MergeQuestion),
        },
    )


@router.get("/projects/{project_id}/episodes", response_model=list[EpisodeSummary])
def list_episodes(
    project_id: uuid.UUID, session: Session = Depends(get_session)
) -> list[EpisodeSummary]:
    """The episode → scene tree down the left of the Story tab."""
    out = []
    episodes = session.scalars(
        select(Episode).where(Episode.project_id == project_id).order_by(Episode.number)
    )
    for episode in episodes:
        scenes = []
        for scene in session.scalars(
            select(Scene).where(Scene.episode_id == episode.id).order_by(Scene.number, Scene.id)
        ):
            scenes.append(
                SceneSummary(
                    id=scene.id,
                    number=scene.number,
                    number_label=scene.number_label or str(scene.number),
                    heading=scene.heading_raw,
                    int_ext=str(scene.int_ext) if scene.int_ext else None,
                    time_of_day=str(scene.time_of_day),
                    story_day=scene.story_day,
                    location_raw=scene.location_raw,
                    sub_area_raw=scene.sub_area_raw,
                    intercut_group=scene.intercut_group,
                    character_count=session.scalar(
                        select(func.count()).select_from(SceneCharacter)
                        .where(SceneCharacter.scene_id == scene.id)
                    ) or 0,
                    dialogue_count=session.scalar(
                        select(func.count()).select_from(DialogueLine)
                        .where(DialogueLine.scene_id == scene.id)
                    ) or 0,
                    gap_count=len(scene.gaps or []),
                )
            )
        out.append(
            EpisodeSummary(
                id=episode.id,
                number=episode.number,
                label=episode.label,
                logline=episode.logline,
                scenes=scenes,
            )
        )
    return out


@router.get("/scenes/{scene_id}", response_model=SceneDetail)
def get_scene(scene_id: uuid.UUID, session: Session = Depends(get_session)) -> SceneDetail:
    scene = session.get(Scene, scene_id)
    if scene is None:
        raise HTTPException(404, "scene not found")
    episode = session.get(Episode, scene.episode_id)

    characters = []
    for link in session.scalars(
        select(SceneCharacter).where(SceneCharacter.scene_id == scene_id)
    ):
        character = session.get(Character, link.character_id)
        if character:
            characters.append(
                {
                    "id": str(character.id),
                    "name": character.name,
                    "tier": str(character.tier) if character.tier else None,
                    "speaks": link.speaks,
                }
            )

    dialogue = [
        DialogueOut(
            order=line.order,
            speaker=line.speaker_raw,
            line=line.line,
            delivery_action=line.delivery_action,
            delivery_emotion=line.delivery_emotion,
        )
        for line in session.scalars(
            select(DialogueLine).where(DialogueLine.scene_id == scene_id)
            .order_by(DialogueLine.order)
        )
    ]

    return SceneDetail(
        id=scene.id,
        episode_number=episode.number if episode else 0,
        number_label=scene.number_label or str(scene.number),
        heading=scene.heading_raw,
        int_ext=str(scene.int_ext) if scene.int_ext else None,
        time_of_day=str(scene.time_of_day),
        story_day=scene.story_day,
        story_day_hint=scene.story_day_hint,
        location_raw=scene.location_raw,
        sub_area_raw=scene.sub_area_raw,
        intercut_group=scene.intercut_group,
        action_text=scene.action_text,
        summary=scene.summary,
        emotional_beat=scene.emotional_beat,
        transition_out=scene.transition_out,
        source_span=scene.source_span or {},
        characters=characters,
        dialogue=dialogue,
        breakdown=scene.breakdown_json or {},
        gaps=scene.gaps or [],
    )


@router.get("/projects/{project_id}/entities", response_model=list[EntityOut])
def list_entities(
    project_id: uuid.UUID,
    entity_type: str = Query("character"),
    session: Session = Depends(get_session),
) -> list[EntityOut]:
    out: list[EntityOut] = []
    if entity_type == "character":
        for character in session.scalars(
            select(Character).where(Character.project_id == project_id)
            .order_by(Character.line_count.desc())
        ):
            out.append(
                EntityOut(
                    id=character.id,
                    name=character.name,
                    entity_type="character",
                    tier=str(character.tier) if character.tier else None,
                    scene_count=character.scene_count,
                    line_count=character.line_count,
                    surfaces=(character.spec_json or {}).get("surfaces", []),
                    gaps=character.gaps or [],
                )
            )
    elif entity_type == "sub_area":
        for area in session.scalars(
            select(LocationSubArea).where(LocationSubArea.project_id == project_id)
            .order_by(LocationSubArea.scene_count.desc())
        ):
            out.append(
                EntityOut(
                    id=area.id, name=area.name, entity_type="sub_area", tier=None,
                    scene_count=area.scene_count, line_count=0,
                    surfaces=(area.spec_json or {}).get("surfaces", []),
                    gaps=area.gaps or [],
                )
            )
    else:
        for prop in session.scalars(
            select(Prop).where(Prop.project_id == project_id)
            .order_by(Prop.scene_count.desc())
        ):
            if entity_type != "prop" and str(prop.kind) != entity_type:
                continue
            out.append(
                EntityOut(
                    id=prop.id, name=prop.name, entity_type=str(prop.kind), tier=None,
                    scene_count=prop.scene_count, line_count=0,
                    surfaces=(prop.spec_json or {}).get("surfaces", []),
                    gaps=prop.gaps or [],
                )
            )
    return out


@router.get("/projects/{project_id}/questions", response_model=list[QuestionOut])
def list_questions(
    project_id: uuid.UUID, session: Session = Depends(get_session)
) -> list[QuestionOut]:
    """G1. These block nothing technically, but every one changes a build count."""
    return [
        QuestionOut(
            id=question.id,
            entity_type=question.entity_type,
            question=question.question,
            candidates=question.candidates_json or [],
            recommendation=question.recommendation,
            resolved=question.resolved,
            decision=question.decision,
        )
        for question in session.scalars(
            select(MergeQuestion).where(MergeQuestion.project_id == project_id)
            .order_by(MergeQuestion.resolved, MergeQuestion.created_at)
        )
    ]


@router.get("/projects/{project_id}/gaps", response_model=list[GapOut])
def list_gaps(
    project_id: uuid.UUID, session: Session = Depends(get_session)
) -> list[GapOut]:
    """Everything the script does not say. Each blocks a build until decided."""
    out: list[GapOut] = []
    rows = session.execute(
        select(Scene, Episode.number)
        .join(Episode, Scene.episode_id == Episode.id)
        .where(Episode.project_id == project_id)
        .order_by(Episode.number, Scene.number)
    )
    for scene, episode_number in rows:
        for text in scene.gaps or []:
            out.append(
                GapOut(
                    scene_id=scene.id,
                    scene_label=f"EP{episode_number} SC{scene.number_label}",
                    episode=episode_number,
                    text=text,
                    kind="scene",
                )
            )
    return out


@router.get("/projects/{project_id}/design-order", response_model=list[BuildItemOut])
def list_design_order(
    project_id: uuid.UUID,
    entity_type: str | None = Query(None),
    session: Session = Depends(get_session),
) -> list[BuildItemOut]:
    stmt = select(Asset).where(Asset.project_id == project_id)
    if entity_type:
        stmt = stmt.where(Asset.entity_type == entity_type)
    return [
        BuildItemOut(
            id=asset.id,
            entity_type=asset.entity_type,
            entity_name=asset.descriptor_text or asset.tag,
            variant_label=asset.variant_label,
            plate_type=str(asset.plate_type) if asset.plate_type else None,
            reason=asset.reason,
            tag=asset.tag,
            status=str(asset.status),
            scene_count=len(asset.scenes_mark or []),
            gaps=asset.gaps or [],
        )
        for asset in session.scalars(stmt.order_by(Asset.entity_type, Asset.tag))
    ]


@router.get("/projects/{project_id}/assets", response_model=list[AssetOut])
def list_assets(
    project_id: uuid.UUID,
    entity_type: str = Query("character"),
    only_generated: bool = Query(True),
    session: Session = Depends(get_session),
) -> list[AssetOut]:
    """Assets with their candidate images — one group per grid row.

    `only_generated` keeps the grid to things that have pictures. The full
    planned list is the design order, which is a different view.
    """
    stmt = select(Asset).where(
        Asset.project_id == project_id, Asset.entity_type == entity_type
    )
    out: list[AssetOut] = []

    for asset in session.scalars(stmt.order_by(Asset.kind, Asset.tag)):
        generations = list(
            session.scalars(
                select(Generation)
                .where(
                    Generation.target_type == "asset",
                    Generation.target_id == asset.id,
                    Generation.status == GenerationStatus.SUCCEEDED,
                )
                .order_by(Generation.created_at)
            )
        )
        if only_generated and not generations:
            continue

        candidates: list[CandidateOut] = []
        prompt = prompt_sections = model = None
        for generation in generations:
            prompt = prompt or generation.prompt_text
            prompt_sections = prompt_sections or generation.prompt_sections_json
            model = model or generation.model
            review = session.scalar(
                select(Review).where(Review.generation_id == generation.id)
                .order_by(Review.created_at.desc())
            )
            for output in generation.outputs_json or []:
                candidates.append(
                    CandidateOut(
                        generation_id=generation.id,
                        # Served through the API so the bucket stays private.
                        url=f"/api/images/{generation.id}",
                        width=output.get("width"),
                        height=output.get("height"),
                        status=str(generation.status),
                        verdict=str(review.verdict) if review else None,
                        reason=review.reason if review else None,
                        category=str(review.category) if review and review.category else None,
                        chosen=asset.approved_version_id is not None
                        and str(generation.id) == str(asset.approved_version_id),
                    )
                )

        spec: dict = {}
        if asset.entity_type == "character":
            character = session.get(Character, asset.entity_id)
            if character:
                spec = character.spec_json or {}

        out.append(
            AssetOut(
                id=asset.id,
                entity_type=asset.entity_type,
                entity_name=asset.descriptor_text or asset.tag,
                variant_label=asset.variant_label,
                kind=str(asset.kind),
                tag=asset.tag,
                status=str(asset.status),
                gaps=asset.gaps or [],
                scene_count=len(asset.scenes_mark or []),
                candidates=candidates,
                spec=spec,
                prompt=prompt,
                prompt_sections=prompt_sections or {},
                model=model,
            )
        )
    return out


@router.get("/images/{generation_id}")
def get_image(generation_id: uuid.UUID, session: Session = Depends(get_session)):
    """Redirect to a short-lived signed URL.

    The bucket stays private: nothing is public, and a link copied out of the
    browser expires rather than becoming a permanent hole.
    """
    generation = session.get(Generation, generation_id)
    if generation is None or not generation.outputs_json:
        raise HTTPException(404, "no image for that generation")

    output = generation.outputs_json[0]
    key = output.get("r2_key")
    if not key:
        # Fall back to the provider URL, which expires on their schedule.
        return RedirectResponse(output["url"])

    layout = get_layout()
    url = get_client().generate_presigned_url(
        "get_object",
        Params={"Bucket": layout.bucket, "Key": key},
        ExpiresIn=3600,
    )
    return RedirectResponse(url)


@router.post("/gates/decisions", response_model=GateDecisionOut)
def record_gate_decision(
    body: GateDecisionIn, session: Session = Depends(get_session)
) -> GateDecisionOut:
    """Record a gate decision and move the element on.

    This is the only thing that unblocks an element: gates are the sole blocking
    point in the system, so writing the decision is what releases downstream
    work. The choice is recorded explicitly rather than inferred — the reference
    provider data had no reliable "chosen" field and finals became
    indistinguishable from attempts (Appendix C).
    """
    asset = session.get(Asset, body.target_id)
    if asset is None:
        raise HTTPException(404, "target not found")

    if body.decision == "approved":
        if body.chosen_generation_id is None:
            raise HTTPException(400, "approving requires a chosen candidate")
        generation = session.get(Generation, body.chosen_generation_id)
        if generation is None or generation.target_id != asset.id:
            raise HTTPException(400, "that candidate does not belong to this element")
        asset.status = ElementStatus.APPROVED
        asset.approved_version_id = body.chosen_generation_id
    elif body.decision == "rejected":
        asset.status = ElementStatus.REJECTED
    else:
        asset.status = ElementStatus.AWAITING_GATE

    decision = GateDecision(
        project_id=asset.project_id,
        gate=Gate(body.gate),
        target_type=body.target_type,
        target_id=body.target_id,
        chosen_generation_id=body.chosen_generation_id,
        decision=GateDecisionKind(body.decision),
        notes=body.notes,
        decided_at=datetime.now(UTC),
    )
    session.add(decision)
    session.flush()

    return GateDecisionOut(
        id=decision.id,
        gate=str(decision.gate),
        target_id=body.target_id,
        chosen_generation_id=body.chosen_generation_id,
        decision=body.decision,
        new_status=str(asset.status),
    )


@router.post("/reviews/{generation_id}/override", response_model=dict)
def override_review(
    generation_id: uuid.UUID, session: Session = Depends(get_session)
) -> dict:
    """Pass an agent-rejected candidate to the gate anyway.

    Overrides are logged because they are the signal used to tune the reviewer:
    a rule the agent keeps enforcing that humans keep overriding is a rule worth
    revisiting.
    """
    generation = session.get(Generation, generation_id)
    if generation is None:
        raise HTTPException(404, "generation not found")
    session.add(
        Review(
            generation_id=generation_id,
            reviewer_kind=ActorKind.USER,
            verdict=ReviewVerdict.OVERRIDE_PASS,
            reason="human override of an agent rejection",
        )
    )
    asset = session.get(Asset, generation.target_id)
    if asset is not None:
        asset.status = ElementStatus.AWAITING_GATE
    return {"ok": True}


@router.get("/projects/{project_id}/shots", response_model=list[SceneShotsOut])
def list_shots(
    project_id: uuid.UUID, session: Session = Depends(get_session)
) -> list[SceneShotsOut]:
    """Episode → scene → shot, with each shot's keyframe candidates.

    The spatial map is returned once per scene rather than per shot, because
    that is what it is: written once and inherited by every shot of the scene.
    """
    out: list[SceneShotsOut] = []
    rows = session.execute(
        select(Scene, Episode.number)
        .join(Episode, Scene.episode_id == Episode.id)
        .where(Episode.project_id == project_id)
        .order_by(Episode.number, Scene.number)
    ).all()

    for scene, episode_number in rows:
        shots = list(
            session.scalars(
                select(Shot).where(Shot.scene_id == scene.id).order_by(Shot.letter)
            )
        )
        if not shots:
            continue

        shot_out: list[ShotOut] = []
        for shot in shots:
            keyframes: list[CandidateOut] = []
            asset = session.scalar(
                select(Asset).where(
                    Asset.entity_type == "keyframe", Asset.entity_id == shot.id
                )
            )
            if asset is not None:
                for generation in session.scalars(
                    select(Generation).where(
                        Generation.target_id == asset.id,
                        Generation.status == GenerationStatus.SUCCEEDED,
                    )
                ):
                    review = session.scalar(
                        select(Review).where(Review.generation_id == generation.id)
                    )
                    for output in generation.outputs_json or []:
                        keyframes.append(
                            CandidateOut(
                                generation_id=generation.id,
                                url=f"/api/images/{generation.id}",
                                width=output.get("width"),
                                height=output.get("height"),
                                status=str(generation.status),
                                verdict=str(review.verdict) if review else None,
                                reason=review.reason if review else None,
                                category=(
                                    str(review.category)
                                    if review and review.category
                                    else None
                                ),
                                chosen=str(generation.id)
                                == str(asset.approved_version_id),
                            )
                        )
            shot_out.append(
                ShotOut(
                    id=shot.id,
                    letter=shot.letter,
                    order=shot.order,
                    duration_s=shot.duration_s,
                    complexity=str(shot.complexity) if shot.complexity else None,
                    is_master_wide=shot.is_master_wide,
                    card=shot.shot_card_json or {},
                    status=str(shot.status),
                    keyframes=keyframes,
                )
            )

        out.append(
            SceneShotsOut(
                scene_id=scene.id,
                episode=episode_number,
                scene_label=scene.number_label or str(scene.number),
                heading=scene.heading_raw,
                spatial_map=shots[0].spatial_map,
                shots=shot_out,
            )
        )
    return out


@router.get("/queue", response_model=list[QueueItem])
def review_queue(session: Session = Depends(get_session)) -> list[QueueItem]:
    """Everything awaiting a human decision, across every project.

    This is the screen that makes "five people, five projects" work: the gates
    are the only blocking points, so a cross-project view of what is blocked is
    the difference between a reviewer hunting and a reviewer clearing.
    """
    items: list[QueueItem] = []
    projects = {p.id: p.name for p in session.scalars(select(Project))}

    # G1 — entity merges, which change the build count.
    for question in session.scalars(
        select(MergeQuestion).where(~MergeQuestion.resolved)
    ):
        items.append(
            QueueItem(
                id=question.id,
                project_id=question.project_id,
                project_name=projects.get(question.project_id, "?"),
                kind="merge_question",
                gate="G1",
                title=question.question[:160],
                subtitle=question.entity_type.replace("_", " "),
                candidates=len(question.candidates_json or []),
                thumbnail=None,
                waiting_since=(
                    question.created_at.isoformat() if question.created_at else None
                ),
            )
        )

    # G2 / G3 — assets with candidates that have passed review and want a pick.
    for asset in session.scalars(
        select(Asset).where(
            Asset.status.in_([ElementStatus.AWAITING_GATE, ElementStatus.AGENT_REVIEW])
        )
    ):
        generations = list(
            session.scalars(
                select(Generation).where(
                    Generation.target_id == asset.id,
                    Generation.status == GenerationStatus.SUCCEEDED,
                )
            )
        )
        if not generations:
            continue
        thumbnail = f"/api/images/{generations[0].id}"
        items.append(
            QueueItem(
                id=asset.id,
                project_id=asset.project_id,
                project_name=projects.get(asset.project_id, "?"),
                kind=asset.entity_type,
                gate="G3" if asset.entity_type == "keyframe" else "G2",
                title=asset.descriptor_text or asset.tag,
                subtitle=asset.variant_label,
                candidates=len(generations),
                thumbnail=thumbnail,
                waiting_since=asset.updated_at.isoformat() if asset.updated_at else None,
            )
        )

    items.sort(key=lambda i: (i.gate, i.waiting_since or ""))
    return items


@router.get("/reports", response_model=ReportsOut)
def reports(
    project_id: uuid.UUID | None = Query(None), session: Session = Depends(get_session)
) -> ReportsOut:
    """'s metrics, computed from the generation log."""
    base = select(Generation)
    if project_id:
        base = base.where(Generation.project_id == project_id)
    generations = list(session.scalars(base))

    by_status: dict[str, int] = {}
    by_agent: dict[str, dict[str, float]] = {}
    cost_by_target: dict[str, float] = {}
    cost_total = tokens_in = tokens_out = 0
    unpriced = 0

    for generation in generations:
        status = str(generation.status)
        by_status[status] = by_status.get(status, 0) + 1

        agent = generation.created_by_agent or "unknown"
        bucket = by_agent.setdefault(agent, {"calls": 0, "cost": 0.0, "tokens": 0})
        bucket["calls"] += 1
        bucket["cost"] += generation.cost_usd or 0.0
        bucket["tokens"] += (generation.tokens_in or 0) + (generation.tokens_out or 0)

        cost = generation.cost_usd
        if cost is None and generation.status == GenerationStatus.SUCCEEDED:
            unpriced += 1
        cost_total += cost or 0.0
        cost_by_target[generation.target_type] = (
            cost_by_target.get(generation.target_type, 0.0) + (cost or 0.0)
        )
        tokens_in += generation.tokens_in or 0
        tokens_out += generation.tokens_out or 0

    # First-pass rate: of elements that got a verdict, how many passed on the
    # first attempt. Counted over elements, not calls — four candidates of one
    # sheet is one decision, not four.
    verdicts = session.execute(
        select(Generation.target_id, Generation.attempt_no, Review.verdict)
        .join(Review, Review.generation_id == Generation.id)
        .where(Review.reviewer_kind == ActorKind.AGENT)
    ).all()
    first_attempt = {t for t, attempt, v in verdicts if attempt == 1 and str(v) == "pass"}
    reviewed = {t for t, _, _ in verdicts}
    first_pass = (len(first_attempt) / len(reviewed)) if reviewed else None

    attempts = session.execute(
        select(Generation.target_id, func.max(Generation.attempt_no))
        .where(Generation.target_type == "asset")
        .group_by(Generation.target_id)
    ).all()
    mean_attempts = (
        sum(a for _, a in attempts) / len(attempts) if attempts else None
    )

    approved = session.scalar(
        select(func.count()).select_from(Asset)
        .where(Asset.status == ElementStatus.APPROVED)
    ) or 0

    categories = session.execute(
        select(Review.category, func.count())
        .where(Review.category.is_not(None))
        .group_by(Review.category)
        .order_by(func.count().desc())
    ).all()

    return ReportsOut(
        total_generations=len(generations),
        by_status=by_status,
        by_agent=by_agent,
        first_pass_rate=first_pass,
        attempts_to_approval=mean_attempts,
        cost_total=round(cost_total, 4),
        cost_by_target_type={k: round(v, 4) for k, v in cost_by_target.items()},
        cost_per_approved_element=(
            round(cost_total / approved, 4) if approved else None
        ),
        top_failure_categories=[
            {"category": str(c), "count": n} for c, n in categories
        ],
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        unpriced_generations=unpriced,
    )


@router.get("/skills", response_model=list[SkillOut])
def list_skills(session: Session = Depends(get_session)) -> list[SkillOut]:
    """The skill registry. Agents resolve by name to the active version."""
    return [
        SkillOut(
            id=skill.id,
            name=skill.name,
            version=skill.version,
            kind=skill.kind,
            source=str(skill.source),
            description=skill.description,
            active=skill.active,
            chars=len(skill.content),
            content_hash=skill.content_hash,
        )
        for skill in session.scalars(select(Skill).order_by(Skill.name, Skill.version))
    ]

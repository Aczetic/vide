"""`python -m vide.shots <command>` — plan shots and generate keyframes.

    plan [--scenes N]        split scenes into shots and write their cards
    keyframes [--shots N]    generate a keyframe per shot
    status                   what is planned, what is waiting on assets

Planning is cheap and safe to run across a whole title. Keyframes are the
expensive step — one image per shot — so they default to a small batch and have
to be asked for explicitly.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

import structlog
from sqlalchemy import func, select

from vide.db import session_scope
from vide.models.asset import Asset, Shot
from vide.models.core import Episode, Project, Scene
from vide.models.enums import ElementStatus
from vide.pipeline.shots import generate_keyframe, plan_project_shots
from vide.registry import load


def _logging(verbose: bool) -> None:
    logging.basicConfig(format="%(message)s", level=logging.INFO if verbose else logging.WARNING)
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="%H:%M:%S"),
            structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.INFO if verbose else logging.WARNING
        ),
    )


def _project():
    with session_scope() as session:
        project = session.scalars(select(Project)).first()
        if project is None:
            raise SystemExit("no project — run the pipeline on a script first")
        return project.id, project.aspect_delivery


def cmd_plan(scenes: int | None, verbose: bool) -> int:
    _logging(verbose)
    project_id, _ = _project()
    print(f"\nplanning shots for {scenes or 'all'} scenes…\n")

    results = asyncio.run(plan_project_shots(project_id, limit=scenes))
    ok = [r for r in results if r.ok]
    cost = sum(r.cost_usd for r in results)
    total_shots = sum(len(r.shots) for r in ok)

    for result in results:
        if result.ok:
            master = sum(1 for s in result.shots if s.card.get("is_master_wide"))
            print(f"  {result.label:<14} {len(result.shots):>2} shots ({master} master wide)")
        else:
            print(f"  {result.label:<14} FAILED: {result.error}")

    print(f"\n  {len(ok)}/{len(results)} scenes planned, {total_shots} shots, ${cost:.2f}")
    missing = {m for r in results for m in r.missing_assets}
    if missing:
        print(f"\n  {len(missing)} distinct assets referenced but not yet approved.")
        print("  Keyframes wait for these — approval is the only thing that blocks.")
    return 0 if ok else 1


def cmd_keyframes(limit: int, candidates: int, verbose: bool) -> int:
    _logging(verbose)
    project_id, aspect = _project()

    with session_scope() as session:
        image_skill, skill_versions = load(session, ["image-prompt"])
        shot_ids = [
            s.id
            for s in session.scalars(
                select(Shot)
                .join(Scene, Shot.scene_id == Scene.id)
                .join(Episode, Scene.episode_id == Episode.id)
                .where(Episode.project_id == project_id)
                .order_by(Episode.number, Scene.number, Shot.letter)
                .limit(limit)
            )
        ]

    if not shot_ids:
        print("  no shots planned — run: python -m vide.shots plan")
        return 1

    print(f"\ngenerating keyframes for {len(shot_ids)} shots…\n")

    async def run():
        made = waiting = failed = 0
        for shot_id in shot_ids:
            batch, missing, error = await generate_keyframe(
                shot_id,
                project_id=project_id,
                image_skill=image_skill,
                skill_versions=skill_versions,
                aspect=aspect,
                candidates=candidates,
            )
            if missing:
                waiting += 1
                print(f"  waiting on approved assets: {', '.join(missing[:3])}")
            elif error:
                failed += 1
                print(f"  failed: {error[:120]}")
            elif batch:
                made += len(batch.succeeded)
                print(f"  {len(batch.succeeded)} keyframe(s)")
        return made, waiting, failed

    made, waiting, failed = asyncio.run(run())
    print(f"\n  {made} keyframes generated, {waiting} waiting on approval, {failed} failed")
    return 0


def cmd_status() -> int:
    project_id, _ = _project()
    with session_scope() as session:
        shots = session.scalar(
            select(func.count()).select_from(Shot)
            .join(Scene, Shot.scene_id == Scene.id)
            .join(Episode, Scene.episode_id == Episode.id)
            .where(Episode.project_id == project_id)
        ) or 0
        keyframes = session.scalar(
            select(func.count()).select_from(Asset)
            .where(Asset.project_id == project_id, Asset.entity_type == "keyframe")
        ) or 0
        approved = session.scalar(
            select(func.count()).select_from(Asset)
            .where(
                Asset.project_id == project_id,
                Asset.status == ElementStatus.APPROVED,
            )
        ) or 0
    print(f"  shots planned:    {shots}")
    print(f"  keyframe assets:  {keyframes}")
    print(f"  approved assets:  {approved}  (keyframes wait on these)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m vide.shots")
    sub = parser.add_subparsers(dest="command", required=True)
    plan = sub.add_parser("plan", help="split scenes into shots")
    plan.add_argument("--scenes", type=int, default=None)
    plan.add_argument("-v", "--verbose", action="store_true")
    keys = sub.add_parser("keyframes", help="generate a keyframe per shot")
    keys.add_argument("--shots", type=int, default=5)
    keys.add_argument("--candidates", type=int, default=2)
    keys.add_argument("-v", "--verbose", action="store_true")
    sub.add_parser("status", help="what is planned and what is waiting")

    args = parser.parse_args(argv)
    match args.command:
        case "plan":
            return cmd_plan(args.scenes, args.verbose)
        case "keyframes":
            return cmd_keyframes(args.shots, args.candidates, args.verbose)
        case "status":
            return cmd_status()
    return 1


if __name__ == "__main__":
    sys.exit(main())

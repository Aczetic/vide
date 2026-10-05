"""`python -m vide.pipeline run <script>` — the extraction chain end to end.

Ingest → breakdown → entity resolution → story-days → continuity matrix →
design order, then a report for a human to review.

The report is the deliverable. Its value is that someone who would have spent
three days reading a script instead spends an hour checking a list — so the
output has to be read, not just stored.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

from vide.db import session_scope
from vide.pipeline.breakdown import extract_all
from vide.pipeline.design_order import DesignOrder, compute_design_order
from vide.pipeline.design_order import to_json as design_json
from vide.pipeline.entities import ResolutionResult, resolve_entities
from vide.pipeline.entities import to_json as entities_json
from vide.pipeline.ingest import ParsedScreenplay, normalise, parse_screenplay
from vide.pipeline.persist import persist_run
from vide.pipeline.story_days import StoryDayResult, infer_story_days
from vide.pipeline.story_days import to_json as days_json
from vide.registry import load

log = structlog.get_logger(__name__)


@dataclass(slots=True)
class RunResult:
    screenplay: ParsedScreenplay
    breakdowns: list[dict[str, Any]]
    entities: ResolutionResult
    story_days: StoryDayResult
    design: DesignOrder
    cost_usd: float = 0.0
    elapsed_s: float = 0.0
    failures: list[str] = field(default_factory=list)


def _configure_logging(verbose: bool) -> None:
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


async def run_m1(
    script: Path,
    *,
    tier: str = "standard",
    concurrency: int = 8,
    persist: bool = True,
    replay: Path | None = None,
) -> RunResult:
    """Run the extraction chain.

    ``replay`` loads breakdowns from a previous run instead of extracting them
    again. Breakdown is ~70 of the ~77 model calls in a full run, so replaying
    turns a $2.20 run into a $0.70 one — and every stage after it is
    deterministic or cheap. Use it for any change that does not alter extraction
    itself: schema work, persistence, entity clustering, the design order.

    Paying to re-derive output you already have in order to test code that did
    not produce it is waste.
    """
    started = time.time()

    document = normalise(script)
    screenplay = parse_screenplay(document.text)
    print(f"  parsed   {screenplay.stats['episodes']} episodes, "
          f"{screenplay.stats['scenes']} scenes, "
          f"{screenplay.stats['dialogue_lines']} dialogue lines")

    with session_scope() as session:
        breakdown_skill, _ = load(session, ["breakdown"])
        entity_skill, _ = load(session, ["entity-resolution"])

    if replay is not None:
        rows = json.loads(replay.read_text())
        by_uid = {s.uid: s for s in screenplay.scenes}
        for row in rows:
            row["time_jump_before"] = getattr(
                by_uid.get(row["uid"]), "time_jump_before", None
            )
        ok, failures, cost = rows, [], 0.0
        print(f"  replayed  {len(rows)} breakdowns from {replay} (no model calls)")
        return await _finish(
            screenplay, rows, started, cost, failures, tier, persist, script, document
        )

    print(f"  extracting breakdowns for {len(screenplay.scenes)} scenes…")
    results = await extract_all(
        screenplay.scenes,
        skill_text=breakdown_skill,
        skill_versions={},
        tier=tier,
        concurrency=concurrency,
    )
    ok = [r for r in results if r.ok]
    failures = [f"{r.episode}:{r.scene_label} — {r.error}" for r in results if not r.ok]
    cost = sum(r.cost_usd or 0 for r in ok)
    print(f"  breakdown {len(ok)}/{len(results)} scenes  (${cost:.2f})")

    by_uid = {s.uid: s for s in screenplay.scenes}
    rows = [
        {
            "episode": r.episode,
            "scene_label": r.scene_label,
            "uid": r.uid,
            "int_ext": r.int_ext,
            "location_raw": r.location_raw,
            "sub_area_raw": r.sub_area_raw,
            "time_of_day": r.time_of_day,
            "heading_cast": r.heading_cast,
            "time_jump_before": by_uid[r.uid].time_jump_before,
            "source_span": r.source_span,
            "extracted": r.extracted,
        }
        for r in ok
    ]

    return await _finish(
        screenplay, rows, started, cost, failures, tier, persist, script, document
    )


async def _finish(
    screenplay, rows, started, cost, failures, tier, persist, script, document
) -> RunResult:
    """Everything after extraction. Shared by a live run and a replay."""
    with session_scope() as session:
        entity_skill, _ = load(session, ["entity-resolution"])

    print("  resolving entities and story-days…")
    entities, story_days = await asyncio.gather(
        resolve_entities(rows, skill_text=entity_skill, tier=tier),
        infer_story_days(rows, tier=tier),
    )
    cost += entities.cost_usd + story_days.cost_usd
    failures += entities.errors
    if story_days.error:
        failures.append(f"story-days — {story_days.error}")

    # A stage that returns nothing must not be allowed to produce a plausible
    # answer. Without story-days the continuity matrix is empty, so no character
    # build is generated — and the design order still prints a confident number
    # that happens to be wrong. Refuse instead.
    scene_loss = 1 - (len(rows) / max(len(screenplay.scenes), 1))
    if not story_days.scene_to_day:
        failures.append(
            "FATAL: story-day inference assigned zero scenes. Every costume build "
            "derives from story-days, so the design order would be missing all "
            "character work. Not computing one."
        )
    elif scene_loss > 0.1:
        failures.append(
            f"FATAL: {scene_loss:.0%} of scenes failed to extract. The build list "
            "would be missing their sets, props and costumes."
        )

    fatal = [f for f in failures if f.startswith("FATAL")]
    design = (
        DesignOrder()
        if fatal
        else compute_design_order(rows, entities.entities, story_days.scene_to_day)
    )

    if persist and not fatal:
        with session_scope() as session:
            outcome = persist_run(
                session,
                name=script.stem.replace("_", " ").title(),
                code=script.stem[:8].upper(),
                screenplay=screenplay,
                breakdowns=rows,
                entities=entities,
                story_days=story_days,
                design=design,
                source_filename=script.name,
                source_text=document.text,
            )
        print(f"  persisted to project {outcome.project_id}")

    return RunResult(
        screenplay=screenplay,
        breakdowns=rows,
        entities=entities,
        story_days=story_days,
        design=design,
        cost_usd=cost,
        elapsed_s=time.time() - started,
        failures=failures,
    )


def write_report(result: RunResult, out: Path) -> None:
    lines: list[str] = []
    add = lines.append

    add("# a later stage review report\n")
    add(f"Ingested {result.screenplay.stats['episodes']} episodes, "
        f"{result.screenplay.stats['scenes']} scenes, "
        f"{result.screenplay.stats['dialogue_lines']} dialogue lines "
        f"in {result.elapsed_s:.0f}s for ${result.cost_usd:.2f}.\n")
    add("Everything below is a proposal. The counts are only final once the "
        "questions in section 2 are answered.\n")

    if result.failures:
        add("## 0. Failures\n")
        for failure in result.failures:
            add(f"- {failure}")
        add("")

    # 1. The build list
    counts = result.design.counts
    add("## 1. Design order — what has to be built\n")
    add(f"**{len(result.design.items)} builds.**\n")
    add("| Type | Builds |")
    add("|---|---:|")
    for kind, count in sorted(counts.items(), key=lambda kv: -kv[1]):
        add(f"| {kind.replace('_', ' ')} | {count} |")
    add("")
    for note in result.design.notes:
        add(f"> {note}\n")

    add("### Build first — heaviest reuse\n")
    add("| Build | Scenes | Episodes |")
    add("|---|---:|---|")
    for item in sorted(result.design.items, key=lambda i: -i.scene_count)[:12]:
        episodes = f"{min(item.episodes)}–{max(item.episodes)}" if item.episodes else "—"
        add(f"| {item.entity_name} — {item.variant_label} | {item.scene_count} | {episodes} |")
    add("")

    # 2. Questions
    add("## 2. Decisions needed (G1)\n")
    add(f"**{len(result.entities.questions)} questions.** Each changes the build "
        "count, and nothing in the script settles them.\n")
    for i, question in enumerate(result.entities.questions, 1):
        add(f"**{i}. [{question.entity_type}]** {question.question}")
        add(f"   - Evidence: {question.evidence}")
        add(f"   - Suggested: {question.recommendation}\n")

    # 3. Story days
    add("## 3. Story-days\n")
    add(f"**{len(result.story_days.days)} days** across "
        f"{len(result.story_days.scene_to_day)} scenes. Costume count is roughly "
        "characters × days, so these boundaries drive the wardrobe build.\n")
    add("| Day | Scenes | Confidence | Evidence |")
    add("|---|---:|---|---|")
    for day in result.story_days.days:
        add(f"| D{day.day} | {len(day.scenes)} | {day.confidence} | {day.evidence[:90]} |")
    add("")

    if result.story_days.contradictions:
        add("### Script contradictions found\n")
        for issue in result.story_days.contradictions:
            add(f"- **{', '.join(issue['scenes'][:6])}** — {issue['problem']}")
        add("")

    # 4. Entities
    add("## 4. Entities\n")
    add("| Type | Entities | From surfaces |")
    add("|---|---:|---:|")
    for kind in ("character", "location", "sub_area", "prop", "vehicle", "screen_insert"):
        entities = result.entities.of(kind)
        surfaces = sum(len(e.surfaces) for e in entities)
        add(f"| {kind.replace('_', ' ')} | {len(entities)} | {surfaces} |")
    add("")

    add("### Characters by tier\n")
    for tier in ("lead", "supporting", "featured", "ensemble", "bit"):
        members = [e for e in result.entities.of("character") if e.tier == tier]
        if members:
            names = ", ".join(
                f"{e.canonical_name} ({e.line_count} lines / {e.scene_count} scenes)"
                for e in sorted(members, key=lambda x: -x.line_count)
            )
            add(f"- **{tier}** ({len(members)}): {names}")
    add("")

    # 5. Gaps
    gaps = [
        (row["uid"], gap)
        for row in result.breakdowns
        for gap in row["extracted"].get("gaps", [])
    ]
    add("## 5. Gaps — script does not say\n")
    add(f"**{len(gaps)} gaps.** Each blocks generating something until decided.\n")
    blockers = [i for i in result.design.items if i.gaps]
    if blockers:
        add("### Builds with nothing to work from\n")
        for item in blockers[:20]:
            add(f"- **{item.entity_name} — {item.variant_label}**: {item.gaps[0]}")
        if len(blockers) > 20:
            add(f"- …and {len(blockers) - 20} more")
        add("")

    out.write_text("\n".join(lines))


def cmd_run(
    script: Path,
    out_dir: Path,
    tier: str,
    concurrency: int,
    verbose: bool,
    persist: bool,
    replay: Path | None = None,
) -> int:
    if not script.exists():
        print(f"  {script} not found")
        return 1
    _configure_logging(verbose)
    # One directory per script per run. A flat --out overwrites the previous
    # title's results the moment a second script is processed.
    run_dir = out_dir / script.stem / time.strftime("%Y%m%d-%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)
    latest = out_dir / script.stem / "latest"
    out_dir = run_dir

    print(f"\nM1 on {script.name}\n")
    result = asyncio.run(
        run_m1(
            script, tier=tier, concurrency=concurrency, persist=persist, replay=replay
        )
    )

    (out_dir / "breakdowns.json").write_text(json.dumps(result.breakdowns, indent=2))
    (out_dir / "entities.json").write_text(entities_json(result.entities))
    (out_dir / "story_days.json").write_text(days_json(result.story_days))
    (out_dir / "design_order.json").write_text(design_json(result.design))
    write_report(result, out_dir / "report.md")

    # `latest` always points at the newest run, so the demo command is stable.
    latest.unlink(missing_ok=True)
    latest.symlink_to(run_dir.name)

    fatal = [f for f in result.failures if f.startswith("FATAL")]
    if fatal:
        print("\n  RUN FAILED — no design order produced:")
        for problem in fatal:
            print(f"    {problem}")
        print(f"\n  report:  {out_dir / 'report.md'}\n")
        return 1

    print(f"\n  {len(result.design.items)} builds, "
          f"{len(result.entities.questions)} decisions needed, "
          f"{len(result.story_days.days)} story-days")
    print(f"  ${result.cost_usd:.2f} in {result.elapsed_s:.0f}s")
    print(f"\n  report:  {out_dir / 'report.md'}\n")
    return 1 if result.failures else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m vide.pipeline")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="run the a later stage chain on a script")
    run.add_argument("script", type=Path)
    run.add_argument("--out", type=Path, default=Path("out"))
    run.add_argument("--tier", default="standard")
    run.add_argument("--concurrency", type=int, default=8)
    run.add_argument("-v", "--verbose", action="store_true")
    run.add_argument(
        "--no-persist", action="store_true", help="write JSON only, skip the database"
    )
    run.add_argument(
        "--replay",
        type=Path,
        help="reuse breakdowns.json from an earlier run instead of re-extracting "
        "(skips ~70 of ~77 model calls)",
    )

    args = parser.parse_args(argv)
    if args.command == "run":
        return cmd_run(
            args.script, args.out, args.tier, args.concurrency, args.verbose,
            persist=not args.no_persist, replay=args.replay,
        )
    return 1


if __name__ == "__main__":
    sys.exit(main())

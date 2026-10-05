"""Continuity matrix and design order.

Pure computation. No model is involved and none should be — this is counting,
and a model asked to count will occasionally get it wrong in a way nobody
notices until the build list is short.

The design order is the answer to "what has to be made". defines it as:

* location sub-areas × time-of-day states
* characters × distinct wardrobe states (≈ character × story-days, minus
  repeats, plus specials such as wedding, wet or bloodied)
* props × states
* screen and insert graphics

It materialises as planned ``Asset`` rows rather than its own table, so the
build list and the production tracker are one object (see README).
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

import structlog

from vide.pipeline.entities import ResolvedEntity

log = structlog.get_logger(__name__)

#: State words that force a separate asset even inside one story-day, because
#: they change what the character looks like mid-day.
_SPECIAL_STATES = {
    "wedding": ("wedding", "bride", "groom", "veil"),
    "bloodied": ("blood", "bloodied", "bleeding", "wound", "injured", "beaten"),
    "wet": ("wet", "soaked", "rain", "drenched", "river"),
    "hospital": ("hospital", "gown", "patient", "bandage", "iv"),
    "dirty": ("dirty", "dishevelled", "disheveled", "muddy", "torn"),
    "unshaven": ("unshaven", "stubble", "homeless", "unkempt"),
    "formal": ("suit", "tuxedo", "gown", "elegant", "formal"),
}


@dataclass(slots=True)
class ContinuityCell:
    """One cell of the story-day × character matrix."""

    story_day: int
    character: str
    scenes: list[str] = field(default_factory=list)
    episodes: list[int] = field(default_factory=list)
    wardrobe_cues: list[str] = field(default_factory=list)
    state_cues: list[str] = field(default_factory=list)
    special_states: list[str] = field(default_factory=list)

    @property
    def variant_label(self) -> str:
        if self.special_states:
            return f"D{self.story_day} — {'/'.join(sorted(self.special_states))}"
        return f"D{self.story_day}"

    @property
    def has_description(self) -> bool:
        return bool(self.wardrobe_cues)


@dataclass(slots=True)
class BuildItem:
    """One thing that has to be designed and generated."""

    entity_type: str
    entity_name: str
    variant_label: str
    #: Why this exists as a separate build.
    reason: str
    scenes: list[str] = field(default_factory=list)
    episodes: list[int] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)
    plate_type: str | None = None
    #: Heaviest-reuse items are built first; a mistake there propagates furthest.
    priority: int = 100

    @property
    def scene_count(self) -> int:
        return len(self.scenes)


#: Phrases the extractor uses when it inferred an object rather than reading one.
#: "knife (implied by 'slices through the cake')" is a correct extraction and a
#: wrong build: nobody designs a knife that is never seen.
_HEDGES = (
    "implied", "implied by", "referenced", "mentioned", "unseen", "off-screen",
    "offscreen", "suggested by", "not shown", "alluded",
)

#: Objects a set is dressed with rather than hero props that need their own sheet.
_SET_DRESSING = {
    "chair", "chairs", "table", "tables", "door", "doors", "window", "windows",
    "curtain", "curtains", "wall", "floor", "ceiling", "lamp", "light", "lights",
    "sofa", "couch", "bed", "desk", "carpet", "rug", "stairs", "staircase",
    "food", "drink", "water", "cup", "glass", "plate", "cutlery", "napkin",
}


@dataclass(slots=True)
class DesignOrder:
    items: list[BuildItem] = field(default_factory=list)
    continuity: list[ContinuityCell] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    #: Things found but judged not worth their own build, with the reason. Kept
    #: visible so a reviewer can promote one rather than discovering it missing.
    deferred: list[str] = field(default_factory=list)

    def of(self, entity_type: str) -> list[BuildItem]:
        return [i for i in self.items if i.entity_type == entity_type]

    @property
    def counts(self) -> dict[str, int]:
        out: dict[str, int] = defaultdict(int)
        for item in self.items:
            out[item.entity_type] += 1
        return dict(out)


def _is_material(entity: ResolvedEntity) -> tuple[bool, str]:
    """Does this deserve its own build?

    Three things get filtered out. An object the extractor only inferred and
    that appears once is probably never on screen. Generic set dressing belongs
    to the location plate, not a prop sheet. Everything recurring survives
    regardless — reuse is itself evidence that it matters.
    """
    if entity.scene_count >= 2:
        return True, ""

    name = entity.canonical_name.lower()
    surfaces = " ".join(entity.surfaces).lower()

    if any(hedge in surfaces for hedge in _HEDGES):
        return False, "only ever inferred, never described; one scene"

    words = {w.strip(".,()'\"") for w in name.split()}
    if words & _SET_DRESSING and len(words) <= 2:
        return False, "generic set dressing — belongs to the location plate"

    return True, ""


def _detect_special_states(texts: list[str]) -> list[str]:
    blob = " ".join(texts).lower()
    return sorted(
        name for name, words in _SPECIAL_STATES.items() if any(w in blob for w in words)
    )


def _name_matches(cue: str, character: str) -> bool:
    """Does a wardrobe cue mention this character?"""
    first = character.split()[0].lower()
    return bool(re.search(rf"\b{re.escape(first)}\b", cue.lower()))


def build_continuity(
    breakdowns: list[dict[str, Any]],
    characters: list[ResolvedEntity],
    scene_to_day: dict[str, int],
) -> list[ContinuityCell]:
    """story-day × character → wardrobe, state."""
    surface_to_entity: dict[str, str] = {}
    for entity in characters:
        for surface in entity.surfaces:
            surface_to_entity[surface.lower()] = entity.canonical_name

    cells: dict[tuple[int, str], ContinuityCell] = {}
    by_scene = {r["uid"]: r for r in breakdowns}

    for scene_key, row in by_scene.items():
        day = scene_to_day.get(scene_key)
        if day is None:
            continue
        extracted = row.get("extracted", {})
        present = {
            surface_to_entity.get(entry.get("name_raw", "").lower())
            for entry in extracted.get("characters_present", [])
        } | {surface_to_entity.get(n.lower()) for n in row.get("heading_cast", [])}

        wardrobe = extracted.get("wardrobe_cues", [])
        states = extracted.get("state_cues", [])

        for name in filter(None, present):
            cell = cells.setdefault(
                (day, name), ContinuityCell(story_day=day, character=name)
            )
            cell.scenes.append(scene_key)
            if row["episode"] not in cell.episodes:
                cell.episodes.append(row["episode"])
            # Only attribute a cue to this character when it names them, or when
            # they are the only person in the scene.
            for cue in wardrobe:
                if _name_matches(cue, name) or len(list(filter(None, present))) == 1:
                    if cue not in cell.wardrobe_cues:
                        cell.wardrobe_cues.append(cue)
            for cue in states:
                if _name_matches(cue, name) or len(list(filter(None, present))) == 1:
                    if cue not in cell.state_cues:
                        cell.state_cues.append(cue)

    for cell in cells.values():
        cell.special_states = _detect_special_states(cell.wardrobe_cues + cell.state_cues)

    return sorted(cells.values(), key=lambda c: (c.story_day, c.character))


def compute_design_order(
    breakdowns: list[dict[str, Any]],
    entities: dict[str, list[ResolvedEntity]],
    scene_to_day: dict[str, int],
) -> DesignOrder:
    order = DesignOrder()
    by_scene = {r["uid"]: r for r in breakdowns}

    # -- locations: sub-area × time-of-day ---------------------------------
    # 's two tiers: one geography master per set, then a coverage plate
    # per time of day. Lighting states are edits of the master, not new builds,
    # but each still needs its own approved asset.
    sub_area_times: dict[str, set[str]] = defaultdict(set)
    sub_area_scenes: dict[str, list[str]] = defaultdict(list)

    for entity in entities.get("location", []) + entities.get("sub_area", []):
        for scene_key in entity.scenes:
            row = by_scene.get(scene_key)
            if row is None:
                continue
            sub_area_times[entity.canonical_name].add(row.get("time_of_day") or "UNSPECIFIED")
            sub_area_scenes[entity.canonical_name].append(scene_key)

    for name, times in sorted(sub_area_times.items()):
        scenes = sub_area_scenes[name]
        episodes = sorted({int(s.split(":")[0]) for s in scenes})
        # Heaviest-reuse set first: everything else on that estate inherits it.
        priority = max(1, 100 - len(scenes))
        order.items.append(
            BuildItem(
                entity_type="location",
                entity_name=name,
                variant_label="geography master",
                plate_type="geography_master",
                reason=f"used in {len(scenes)} scene(s); every coverage plate inherits it",
                scenes=scenes,
                episodes=episodes,
                priority=priority,
            )
        )
        for time_of_day in sorted(times):
            order.items.append(
                BuildItem(
                    entity_type="location",
                    entity_name=name,
                    variant_label=f"coverage plate — {time_of_day.lower()}",
                    plate_type="coverage_plate",
                    reason=f"{time_of_day.lower()} state of this set",
                    scenes=[
                        s
                        for s in scenes
                        if (by_scene[s].get("time_of_day") or "UNSPECIFIED") == time_of_day
                    ],
                    episodes=episodes,
                    priority=priority + 1,
                )
            )

    # -- characters: one build per story-day state -------------------------
    continuity = build_continuity(breakdowns, entities.get("character", []), scene_to_day)
    order.continuity = continuity

    seen_master: set[str] = set()
    for cell in continuity:
        if cell.character not in seen_master:
            seen_master.add(cell.character)
            order.items.append(
                BuildItem(
                    entity_type="character",
                    entity_name=cell.character,
                    variant_label="master sheet",
                    reason="face and body lock; every state inherits it",
                    scenes=list(cell.scenes),
                    episodes=list(cell.episodes),
                    priority=1,
                    gaps=(
                        []
                        if cell.has_description
                        else [
                            "no wardrobe or physical description anywhere in the "
                            "script — the face must be decided here before any "
                            "other state can be generated"
                        ]
                    ),
                )
            )
        order.items.append(
            BuildItem(
                entity_type="character",
                entity_name=cell.character,
                variant_label=cell.variant_label,
                reason=(
                    f"story-day {cell.story_day}"
                    + (f", {'/'.join(cell.special_states)}" if cell.special_states else "")
                ),
                scenes=list(cell.scenes),
                episodes=list(cell.episodes),
                priority=10,
                gaps=(
                    []
                    if cell.has_description
                    else [f"no wardrobe described for D{cell.story_day}"]
                ),
            )
        )

    # -- props, vehicles, screen inserts -----------------------------------
    for kind, label in (("prop", "prop sheet"), ("screen_insert", "insert graphic")):
        for entity in entities.get(kind, []):
            material, why = _is_material(entity)
            if not material:
                order.deferred.append(
                    f"{entity.canonical_name} ({kind}) — {why}"
                )
                continue
            order.items.append(
                BuildItem(
                    entity_type=kind,
                    entity_name=entity.canonical_name,
                    variant_label=label,
                    reason=f"appears in {entity.scene_count} scene(s)",
                    scenes=list(entity.scenes),
                    episodes=sorted(entity.episodes),
                    priority=50,
                )
            )

    # A vehicle is three builds: exterior, and front and back interiors, which
    # are separate assets.
    for entity in entities.get("vehicle", []):
        interior_seen = any(
            v.get("interior_seen")
            for key in entity.scenes
            for v in by_scene.get(key, {}).get("extracted", {}).get("vehicles", [])
        )
        order.items.append(
            BuildItem(
                entity_type="vehicle",
                entity_name=entity.canonical_name,
                variant_label="exterior",
                reason=f"appears in {entity.scene_count} scene(s)",
                scenes=list(entity.scenes),
                episodes=sorted(entity.episodes),
                priority=60,
            )
        )
        if interior_seen:
            for part in ("interior front", "interior back"):
                order.items.append(
                    BuildItem(
                        entity_type="vehicle",
                        entity_name=entity.canonical_name,
                        variant_label=part,
                        reason="interior is seen; a car interior is its own asset",
                        scenes=list(entity.scenes),
                        episodes=sorted(entity.episodes),
                        priority=61,
                    )
                )

    order.items.sort(key=lambda i: (i.priority, i.entity_type, i.entity_name))

    no_description = sum(1 for i in order.items if i.gaps)
    if no_description:
        order.notes.append(
            f"{no_description} build(s) have no description in the script and need "
            "a decision before they can be prompted"
        )
    if order.deferred:
        order.notes.append(
            f"{len(order.deferred)} item(s) found but not given their own build — "
            "inferred-once objects and generic set dressing. Listed under "
            "`deferred` so any can be promoted."
        )

    log.info("design_order.computed", total=len(order.items), **order.counts)
    return order


def to_json(order: DesignOrder) -> str:
    return json.dumps(
        {
            "counts": order.counts,
            "total": len(order.items),
            "notes": order.notes,
            "deferred": order.deferred,
            "items": [
                {
                    "entity_type": i.entity_type,
                    "entity_name": i.entity_name,
                    "variant_label": i.variant_label,
                    "plate_type": i.plate_type,
                    "reason": i.reason,
                    "scene_count": i.scene_count,
                    "episodes": i.episodes,
                    "priority": i.priority,
                    "gaps": i.gaps,
                }
                for i in order.items
            ],
            "continuity": [
                {
                    "story_day": c.story_day,
                    "character": c.character,
                    "variant": c.variant_label,
                    "episodes": c.episodes,
                    "scene_count": len(c.scenes),
                    "wardrobe_cues": c.wardrobe_cues,
                    "state_cues": c.state_cues,
                    "special_states": c.special_states,
                }
                for c in order.continuity
            ],
        },
        indent=2,
    )

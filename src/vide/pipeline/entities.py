"""Entity resolution: raw strings → canonical entities.

specifies embedding similarity to propose clusters with an LLM confirming.
**This implementation clusters with the LLM directly and does not embed.** The
reason is scale: the reference title yields 40 distinct character strings, ~50
location strings and ~110 prop strings. At that size an embedding pass adds a
dependency and a credential while making the result *worse* — cosine distance
cannot tell that "server room" and "server floor" is a design decision rather
than a spelling variant, which is exactly the judgement that matters here.

Embeddings earn their place when a title produces thousands of distinct
surfaces and a single call no longer fits or no longer reasons well. The
``entity_surfaces`` table already carries an embedding column for that day; it
is left null until then.

Tiers and counts are computed, not asked for — arithmetic the model should not
be doing.
"""

from __future__ import annotations

import asyncio
import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

import structlog

from vide.models.enums import CharacterTier
from vide.providers import LLMRequest, LogContext, TaskType, get_registry

log = structlog.get_logger(__name__)

#: Strings that name a role rather than a person, and can hide several entities.
_GENERIC = {
    "goons", "guards", "businessmen", "crowd", "men", "people", "police",
    "cops", "nurses", "doctors", "staff", "servants", "waiters",
}


@dataclass(slots=True)
class Surface:
    """One distinct raw string and where it occurred."""

    text: str
    scenes: list[str] = field(default_factory=list)
    mentions: int = 0
    lines: int = 0

    @property
    def episodes(self) -> set[int]:
        return {int(s.split(":")[0]) for s in self.scenes}


@dataclass(slots=True)
class ResolvedEntity:
    canonical_name: str
    entity_type: str
    surfaces: list[str]
    scenes: list[str]
    mention_count: int
    line_count: int = 0
    tier: str | None = None
    confidence: str = "certain"
    note: str | None = None

    @property
    def scene_count(self) -> int:
        return len(set(self.scenes))

    @property
    def episodes(self) -> set[int]:
        return {int(s.split(":")[0]) for s in self.scenes}


@dataclass(slots=True)
class MergeQuestion:
    """An explicit G1 question. Not answered by extraction — a design call."""

    entity_type: str
    question: str
    candidates: list[str]
    evidence: str
    recommendation: str


@dataclass(slots=True)
class ResolutionResult:
    entities: dict[str, list[ResolvedEntity]] = field(default_factory=dict)
    questions: list[MergeQuestion] = field(default_factory=list)
    cost_usd: float = 0.0
    errors: list[str] = field(default_factory=list)

    def of(self, entity_type: str) -> list[ResolvedEntity]:
        return self.entities.get(entity_type, [])


CLUSTER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "entities": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "canonical_name": {"type": "string"},
                    "surfaces": {"type": "array", "items": {"type": "string"}},
                    "confidence": {
                        "type": "string",
                        "enum": ["certain", "likely", "uncertain"],
                    },
                    "note": {
                        "type": ["string", "null"],
                        "description": "Reasoning, required when confidence is not certain.",
                    },
                },
                "required": ["canonical_name", "surfaces", "confidence", "note"],
                "additionalProperties": False,
            },
        },
        "questions": {
            "type": "array",
            "description": "Merges that are design decisions. Do not resolve these.",
            "items": {
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "candidates": {"type": "array", "items": {"type": "string"}},
                    "evidence": {"type": "string"},
                    "recommendation": {"type": "string"},
                },
                "required": ["question", "candidates", "evidence", "recommendation"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["entities", "questions"],
    "additionalProperties": False,
}


def collect_surfaces(breakdowns: list[dict[str, Any]]) -> dict[str, dict[str, Surface]]:
    """Gather distinct strings per entity type, with where each occurred."""
    out: dict[str, dict[str, Surface]] = defaultdict(dict)

    def add(kind: str, text: str, scene: str, lines: int = 0) -> None:
        text = (text or "").strip()
        if not text:
            return
        key = text.lower()
        surface = out[kind].setdefault(key, Surface(text=text))
        surface.mentions += 1
        surface.lines += lines
        if scene not in surface.scenes:
            surface.scenes.append(scene)

    for row in breakdowns:
        scene = row["uid"]
        extracted = row.get("extracted", {})

        for name in row.get("heading_cast", []):
            add("character", name, scene)
        for entry in extracted.get("characters_present", []):
            add("character", entry.get("name_raw", ""), scene)

        if row.get("location_raw"):
            add("location", row["location_raw"], scene)
        if row.get("sub_area_raw"):
            add("sub_area", row["sub_area_raw"], scene)
        for entry in extracted.get("implied_locations", []):
            add("sub_area", entry.get("name_raw", ""), scene)

        for prop in extracted.get("props", []):
            add("prop", prop, scene)
        for entry in extracted.get("vehicles", []):
            add("vehicle", entry.get("name_raw", ""), scene)
        for insert in extracted.get("screens_inserts", []):
            add("screen_insert", insert, scene)

    return {k: dict(v) for k, v in out.items()}


def _render_surfaces(surfaces: dict[str, Surface], line_counts: dict[str, int]) -> str:
    rows = sorted(surfaces.values(), key=lambda s: (-s.mentions, s.text.lower()))
    lines = []
    for surface in rows:
        episodes = sorted(surface.episodes)
        span = (
            f"EP{episodes[0]}"
            if len(episodes) == 1
            else f"EP{episodes[0]}-{episodes[-1]} ({len(episodes)} eps)"
        )
        extra = ""
        spoken = line_counts.get(surface.text.upper(), 0)
        if spoken:
            extra = f", {spoken} spoken lines"
        lines.append(
            f"- {surface.text!r} — {surface.mentions} mention(s) across "
            f"{len(surface.scenes)} scene(s), {span}{extra}"
        )
    return "\n".join(lines)


#: Above this many surfaces, cluster in batches. A single call over a hundred-plus
#: strings spends most of its budget reasoning and truncates mid-JSON — and the
#: list only grows with episode count.
CHUNK_THRESHOLD = 55
CHUNK_SIZE = 40

_STOPWORDS = {"a", "an", "the", "of", "in", "on", "at", "to", "with", "and"}


def _group_key(text: str) -> str:
    """First meaningful word, so variants of one thing land in the same batch.

    "phone", "her phone" and "phone (his)" must be judged together or
    the merge cannot be seen. Keying on the last word catches the possessive
    forms that a plain alphabetical sort would scatter.
    """
    words = [w.strip("'’\"()[],.").lower() for w in text.split()]
    words = [w for w in words if w and w not in _STOPWORDS]
    if not words:
        return text.lower()
    # Possessive prefixes ("a named character's phone") hide the head noun at the end.
    if len(words) > 1 and (words[0].endswith("'s") or words[0].endswith("s'")):
        return words[-1]
    return words[0]


def _chunk_surfaces(surfaces: dict[str, Surface]) -> list[dict[str, Surface]]:
    """Batch surfaces, keeping likely-related strings together."""
    if len(surfaces) <= CHUNK_THRESHOLD:
        return [surfaces]

    groups: dict[str, list[tuple[str, Surface]]] = defaultdict(list)
    for key, surface in surfaces.items():
        groups[_group_key(surface.text)].append((key, surface))

    chunks: list[dict[str, Surface]] = []
    current: dict[str, Surface] = {}
    for group in sorted(groups.values(), key=len, reverse=True):
        if current and len(current) + len(group) > CHUNK_SIZE:
            chunks.append(current)
            current = {}
        current.update(dict(group))
    if current:
        chunks.append(current)
    return chunks


async def _cluster(
    entity_type: str,
    surfaces: dict[str, Surface],
    line_counts: dict[str, int],
    *,
    skill_text: str,
    tier: str,
) -> tuple[list[ResolvedEntity], list[MergeQuestion], float, str | None]:
    if not surfaces:
        return [], [], 0.0, None

    chunks = _chunk_surfaces(surfaces)
    if len(chunks) > 1:
        log.info(
            "entities.chunked", type=entity_type, surfaces=len(surfaces), chunks=len(chunks)
        )
        results = await asyncio.gather(
            *(
                _cluster_one(entity_type, chunk, line_counts, skill_text=skill_text, tier=tier)
                for chunk in chunks
            )
        )
        entities: list[ResolvedEntity] = []
        questions: list[MergeQuestion] = []
        cost = 0.0
        errors = []
        for chunk_entities, chunk_questions, chunk_cost, error in results:
            entities += chunk_entities
            questions += chunk_questions
            cost += chunk_cost
            if error:
                errors.append(error)
        return entities, questions, cost, "; ".join(errors) or None

    return await _cluster_one(
        entity_type, chunks[0], line_counts, skill_text=skill_text, tier=tier
    )


async def _cluster_one(
    entity_type: str,
    surfaces: dict[str, Surface],
    line_counts: dict[str, int],
    *,
    skill_text: str,
    tier: str,
) -> tuple[list[ResolvedEntity], list[MergeQuestion], float, str | None]:
    registry = get_registry()
    provider, model = registry.resolve(

        tier,

        TaskType.LLM_WRITER,

        log_context=LogContext(agent="entity-resolver", target_type="project"),

    )

    prompt = (
        f"Entity type: {entity_type}\n"
        f"{len(surfaces)} distinct surface strings from a 40-episode screenplay.\n"
        "Cluster them into canonical entities. Every input string must appear in "
        "exactly one entity's `surfaces` list — do not drop any.\n\n"
        f"{_render_surfaces(surfaces, line_counts)}"
    )

    request = LLMRequest(
        task=TaskType.LLM_WRITER,
        system=skill_text,
        user=prompt,
        json_schema=CLUSTER_SCHEMA,
        schema_name="entity_clusters",
        # Deliberately low effort with a large output budget. Clustering is
        # pattern-matching over a list, not deliberation — and thinking tokens
        # count against max_tokens, so a high effort setting here starves the
        # output and truncates the JSON mid-object.
        effort="low",
        max_tokens=24000,
    )

    try:
        response = await provider.complete(request, model)
    except Exception as exc:  # noqa: BLE001
        return [], [], 0.0, f"{entity_type}: {exc!r}"

    parsed = response.parsed or {}
    by_key = {k: s for k, s in surfaces.items()}
    entities: list[ResolvedEntity] = []

    for item in parsed.get("entities", []):
        members = [s for s in item.get("surfaces", []) if s.lower() in by_key]
        if not members:
            continue
        scenes: list[str] = []
        mentions = 0
        lines = 0
        for member in members:
            surface = by_key[member.lower()]
            mentions += surface.mentions
            lines += line_counts.get(surface.text.upper(), 0)
            scenes += [s for s in surface.scenes if s not in scenes]
        entities.append(
            ResolvedEntity(
                canonical_name=item["canonical_name"],
                entity_type=entity_type,
                surfaces=members,
                scenes=scenes,
                mention_count=mentions,
                line_count=lines,
                confidence=item.get("confidence", "certain"),
                note=item.get("note"),
            )
        )

    # Nothing may be silently dropped: an unassigned string is a missing asset.
    assigned = {m.lower() for e in entities for m in e.surfaces}
    for key, surface in surfaces.items():
        if key not in assigned:
            entities.append(
                ResolvedEntity(
                    canonical_name=surface.text,
                    entity_type=entity_type,
                    surfaces=[surface.text],
                    scenes=list(surface.scenes),
                    mention_count=surface.mentions,
                    line_count=line_counts.get(surface.text.upper(), 0),
                    confidence="uncertain",
                    note="not placed by clustering; kept as its own entity for review",
                )
            )

    questions = [
        MergeQuestion(
            entity_type=entity_type,
            question=q["question"],
            candidates=q["candidates"],
            evidence=q["evidence"],
            recommendation=q["recommendation"],
        )
        for q in parsed.get("questions", [])
    ]
    return entities, questions, response.cost_usd or 0.0, None


def assign_tiers(characters: list[ResolvedEntity]) -> None:
    """Tier from scene and line counts. Arithmetic, not judgement."""
    for entity in characters:
        scenes, lines = entity.scene_count, entity.line_count
        if entity.canonical_name.lower() in _GENERIC:
            entity.tier = CharacterTier.BIT
        elif lines >= 40 or scenes >= 15:
            entity.tier = CharacterTier.LEAD
        elif lines >= 15 or scenes >= 8:
            entity.tier = CharacterTier.SUPPORTING
        elif lines >= 5:
            entity.tier = CharacterTier.FEATURED
        elif lines >= 1:
            entity.tier = CharacterTier.ENSEMBLE
        else:
            entity.tier = CharacterTier.BIT


def flag_generic_groups(result: ResolutionResult) -> None:
    """A recurring crowd label may be one asset or several — ask, don't assume."""
    for entity in result.of("character"):
        if entity.canonical_name.lower() in _GENERIC and len(entity.episodes) > 2:
            result.questions.append(
                MergeQuestion(
                    entity_type="character",
                    question=(
                        f"{entity.canonical_name!r} appears across "
                        f"{len(entity.episodes)} episodes — is this one recurring "
                        "group, or different people each time?"
                    ),
                    candidates=[entity.canonical_name],
                    evidence=f"episodes {sorted(entity.episodes)}",
                    recommendation=(
                        "If one crew, build a single group asset with a fixed range "
                        "of heights and wardrobe. If not, each appearance needs its "
                        "own, which changes the build count."
                    ),
                )
            )


async def resolve_entities(
    breakdowns: list[dict[str, Any]],
    *,
    skill_text: str,
    tier: str = "standard",
) -> ResolutionResult:
    surfaces = collect_surfaces(breakdowns)

    line_counts: Counter[str] = Counter()
    for row in breakdowns:
        for entry in row.get("extracted", {}).get("characters_present", []):
            if entry.get("speaks"):
                line_counts[entry.get("name_raw", "").upper()] += 1

    result = ResolutionResult()
    types = ["character", "location", "sub_area", "prop", "vehicle", "screen_insert"]

    outcomes = await asyncio.gather(
        *(
            _cluster(t, surfaces.get(t, {}), line_counts, skill_text=skill_text, tier=tier)
            for t in types
        )
    )

    for entity_type, (entities, questions, cost, error) in zip(types, outcomes, strict=True):
        result.entities[entity_type] = entities
        result.questions += questions
        result.cost_usd += cost
        if error:
            result.errors.append(error)
        log.info(
            "entities.resolved",
            type=entity_type,
            surfaces=len(surfaces.get(entity_type, {})),
            entities=len(entities),
            questions=len(questions),
        )

    assign_tiers(result.of("character"))
    flag_generic_groups(result)
    return result


def to_json(result: ResolutionResult) -> str:
    return json.dumps(
        {
            "entities": {
                kind: [
                    {
                        "canonical_name": e.canonical_name,
                        "surfaces": e.surfaces,
                        "scenes": e.scenes,
                        "scene_count": e.scene_count,
                        "mention_count": e.mention_count,
                        "line_count": e.line_count,
                        "tier": e.tier,
                        "confidence": e.confidence,
                        "note": e.note,
                    }
                    for e in entities
                ]
                for kind, entities in result.entities.items()
            },
            "questions": [
                {
                    "entity_type": q.entity_type,
                    "question": q.question,
                    "candidates": q.candidates,
                    "evidence": q.evidence,
                    "recommendation": q.recommendation,
                }
                for q in result.questions
            ],
        },
        indent=2,
    )

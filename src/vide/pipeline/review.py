"""Image review agent.

Looks at a generated image against its spec and the named rules, and returns a
verdict with a reason in plain language and a failure category. The category is
the point: it decides what gets changed next. "It looks wrong" sends
someone guessing; `rule_violation: heads still on the full-body panels` says
exactly which prompt section to patch.

Rejections are never deleted. They go to the agent-rejected view where a human
can override, and those overrides are what tune the agent later.

The first real sheet this pipeline produced broke two rules the prompt had
explicitly asked for — the heads were still on the full-body panels, and the
hands were not empty. Image models drop instructions; that is precisely why a
reviewer exists rather than trusting the prompt.
"""

from __future__ import annotations

import asyncio
import base64
import uuid
from dataclasses import dataclass, field
from typing import Any

import structlog
from sqlalchemy import select

from vide.db import session_scope
from vide.models.asset import Asset
from vide.models.enums import ActorKind, ElementStatus, FailureCategory, ReviewVerdict
from vide.models.generation import Generation, Review
from vide.providers import ImageInput, LLMRequest, LogContext, TaskType, get_registry
from vide.storage import get_client, get_layout

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

REVIEW_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["pass", "fail"]},
        "reason": {
            "type": "string",
            "description": (
                "Plain language, for a reviewer to read. What is wrong and where, "
                "or what is right. One or two sentences."
            ),
        },
        # Strict schema mode rejects union types such as ["string", "null"],
        # so "none" is a sentinel rather than the field being nullable.
        "category": {
            "type": "string",
            "enum": [
                "prompt_ambiguity", "prompt_contradiction", "model_limitation",
                "asset_weakness", "reference_error", "rule_violation", "none",
            ],
            "description": 'Required when the verdict is fail; "none" when it passes.',
        },
        "proposed_fix": {
            "type": "string",
            "description": (
                "The single change to make, or an empty string when it passes. "
                "Name the prompt section when the fix is a prompt fix — a patch "
                "changes one section and leaves the rest byte-identical."
            ),
        },
        "rule_violations": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Named rules this output breaks.",
        },
        "checks": {
            "type": "object",
            "description": "Each structural requirement, observed in the image.",
            "properties": {
                "three_panels": {"type": "boolean"},
                "front_full_body": {"type": "boolean"},
                "back_full_body": {"type": "boolean"},
                "close_portrait": {"type": "boolean"},
                "same_person_across_panels": {"type": "boolean"},
                "heads_removed_from_full_body": {"type": "boolean"},
                "hands_empty": {"type": "boolean"},
                "neutral_grey_background": {"type": "boolean"},
                "no_equipment_visible": {"type": "boolean"},
                "no_rim_light": {"type": "boolean"},
                "matches_spec": {"type": "boolean"},
            },
            "required": [
                "three_panels", "front_full_body", "back_full_body",
                "close_portrait", "same_person_across_panels",
                "heads_removed_from_full_body", "hands_empty",
                "neutral_grey_background", "no_equipment_visible",
                "no_rim_light", "matches_spec",
            ],
            "additionalProperties": False,
        },
    },
    "required": ["verdict", "reason", "category", "proposed_fix", "rule_violations", "checks"],
    "additionalProperties": False,
}

#: Loaded from the skill registry rather than inlined, so it can be
#: versioned and pinned per project. Not included in this repository.
SYSTEM_SKILL = "image-review"
@dataclass(slots=True)
class ReviewResult:
    generation_id: uuid.UUID
    verdict: str = "fail"
    reason: str = ""
    category: str | None = None
    proposed_fix: str | None = None
    rule_violations: list[str] = field(default_factory=list)
    checks: dict[str, bool] = field(default_factory=dict)
    cost_usd: float | None = None
    error: str | None = None

    @property
    def passed(self) -> bool:
        return self.error is None and self.verdict == "pass"


def _render_spec(spec: dict[str, Any], name: str) -> str:
    return (
        f"The sheet should show: {name}\n"
        f"Age and build: {spec.get('age_build', 'unstated')}\n"
        f"Face and body lock: {spec.get('face_body_lock', 'unstated')}\n"
        f"Marks: {', '.join(spec.get('distinguishing_marks', [])) or 'none'}\n"
        f"Hair: {spec.get('hair_grooming', 'unstated')}\n"
        f"Wardrobe: {spec.get('signature_wardrobe', 'unstated')}"
    )


def _fetch_image(generation_id: uuid.UUID) -> tuple[str, str] | None:
    """Pull the image out of R2 as base64.

    The reviewer cannot be given a URL. The bucket is private, and the API that
    serves it runs on localhost — a remote vision model can reach neither. The
    bytes go inline.
    """
    with session_scope() as session:
        generation = session.get(Generation, generation_id)
        if generation is None or not generation.outputs_json:
            return None
        output = generation.outputs_json[0]
        key = output.get("r2_key")

    if not key:
        return None
    body = get_client().get_object(Bucket=get_layout().bucket, Key=key)["Body"].read()
    return base64.b64encode(body).decode(), "image/png"


async def review_generation(
    generation_id: uuid.UUID,
    *,
    spec: dict[str, Any],
    name: str,
    tier: str = "standard",
) -> ReviewResult:
    registry = get_registry()
    provider, model = registry.resolve(

        tier,

        TaskType.LLM_REVIEWER_VISION,

        log_context=LogContext(agent="image-review", target_type="asset"),

    )
    result = ReviewResult(generation_id=generation_id)

    image = await asyncio.to_thread(_fetch_image, generation_id)
    if image is None:
        result.error = "no stored image to review"
        return result
    encoded, media_type = image

    request = LLMRequest(
        task=TaskType.LLM_REVIEWER_VISION,
        system=_system(SYSTEM_SKILL),
        user=f"{_render_spec(spec, name)}\n\nReview the sheet.",
        images=[
            ImageInput(
                base64_data=encoded,
                media_type=media_type,
                label="the generated character sheet",
            )
        ],
        json_schema=REVIEW_SCHEMA,
        schema_name="sheet_review",
        effort="low",
        max_tokens=16000,
    )

    try:
        response = await provider.complete(request, model)
    except Exception as exc:  # noqa: BLE001
        result.error = repr(exc)
        return result

    parsed = response.parsed or {}
    result.verdict = parsed.get("verdict", "fail")
    result.reason = parsed.get("reason", "")
    category = parsed.get("category")
    result.category = None if category in (None, "none", "") else category
    result.proposed_fix = parsed.get("proposed_fix") or None
    result.rule_violations = parsed.get("rule_violations", [])
    result.checks = parsed.get("checks", {})
    result.cost_usd = response.cost_usd
    return result


def _record(session, result: ReviewResult) -> None:
    """Write the verdict. Errors are never recorded as verdicts.

    A failed API call is not "the image is bad". Recording it as a FAIL would
    tell a reviewer the generation was rejected on its merits, and would poison
    the first-pass approval rate the benchmark depends on.
    """
    if result.error is not None:
        return

    session.add(
        Review(
            generation_id=result.generation_id,
            reviewer_kind=ActorKind.AGENT,
            reviewer_agent="image-review",
            verdict=ReviewVerdict.PASS if result.passed else ReviewVerdict.FAIL,
            category=(
                FailureCategory(result.category)
                if result.category and not result.passed
                else None
            ),
            reason=result.reason,
            proposed_fix=result.proposed_fix,
            metrics_json=result.checks,
            rule_violations=result.rule_violations,
        )
    )


async def review_asset(
    asset_id: uuid.UUID, *, concurrency: int = 2
) -> list[ReviewResult]:
    """Review every candidate for one asset, then set the asset's status.

    An asset with at least one passing candidate goes to the gate queue. One
    where everything failed is escalated — a human needs to look, because
    spending another four generations on the same prompt would repeat the
    failure rather than fix it.
    """
    with session_scope() as session:
        asset = session.get(Asset, asset_id)
        if asset is None:
            raise LookupError(f"asset {asset_id} not found")
        name = asset.descriptor_text or asset.tag
        spec: dict[str, Any] = {}
        if asset.entity_type == "character":
            from vide.models.entity import Character

            character = session.get(Character, asset.entity_id)
            if character:
                spec = character.spec_json or {}

        generations = [
            (g.id, g.outputs_json)
            for g in session.scalars(
                select(Generation).where(
                    Generation.target_type == "asset",
                    Generation.target_id == asset_id,
                    Generation.status == "succeeded",
                )
            )
            if g.outputs_json
        ]

    # Capped: a vision call carries a multi-megabyte image, and firing four at
    # once exhausts the provider's in-flight budget rather than going faster.
    semaphore = asyncio.Semaphore(concurrency)

    async def one(generation_id: uuid.UUID) -> ReviewResult:
        async with semaphore:
            return await review_generation(generation_id, spec=spec, name=name)

    results = await asyncio.gather(*(one(gid) for gid, _ in generations))

    reviewed = [r for r in results if r.error is None]
    errored = [r for r in results if r.error is not None]

    with session_scope() as session:
        for result in results:
            _record(session, result)
        row = session.get(Asset, asset_id)
        if any(r.passed for r in reviewed):
            row.status = ElementStatus.AWAITING_GATE
        elif errored and not reviewed:
            # Nothing was actually reviewed. Leave it for another pass rather
            # than escalating a human to look at a problem that does not exist.
            row.status = ElementStatus.AGENT_REVIEW
            row.stale_reason = f"review could not run: {errored[0].error[:200]}"
        elif reviewed:
            row.status = ElementStatus.ESCALATED
            row.stale_reason = "every candidate was rejected by review"

    log.info(
        "review.asset",
        asset=str(asset_id),
        passed=sum(1 for r in reviewed if r.passed),
        failed=sum(1 for r in reviewed if not r.passed),
        errored=len(errored),
    )
    return list(results)

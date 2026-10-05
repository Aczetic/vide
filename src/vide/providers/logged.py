"""Record every model call in the generation log.

Until now only image calls were written to ``generations``. Every LLM call —
breakdowns, entity resolution, specs, prompts, reviews — went unrecorded, which
left the log describing a fraction of the work and made 's reports
(cost per approved element, attempts-to-approval, first-pass approval rate by
skill version) answerable only for images.

Wrapping the provider rather than instrumenting each call site means a new agent
is logged by existing, not by remembering.

Cost: OpenRouter sometimes omits it from the usage block, but always returns a
generation id. ``backfill_costs`` resolves those afterwards against
``GET /generation?id=`` — which an ordinary inference key can reach.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import httpx
import structlog
from sqlalchemy import select

from vide.config import get_settings
from vide.db import session_scope
from vide.models.enums import ActorKind, GenerationStatus
from vide.models.generation import Generation
from vide.providers.base import LLMProvider, LLMRequest, LLMResult, ModelCaps

log = structlog.get_logger(__name__)


@dataclass(slots=True)
class LogContext:
    """What this call is for, so the row can be attributed."""

    project_id: uuid.UUID | None = None
    target_type: str = "llm"
    target_id: uuid.UUID | None = None
    agent: str = "unknown"
    skill_versions: dict | None = None
    attempt_no: int = 1


class LoggedLLMProvider:
    """Wraps an LLMProvider and writes a generations row per call."""

    def __init__(self, inner: LLMProvider, context: LogContext) -> None:
        self._inner = inner
        self._context = context
        self.name = inner.name

    def capabilities(self, model: str) -> ModelCaps:
        return self._inner.capabilities(model)

    async def complete(self, req: LLMRequest, model: str) -> LLMResult:
        context = self._context
        try:
            result = await self._inner.complete(req, model)
        except Exception as exc:
            # A failed call is part of the record: attempts-to-approval is wrong
            # if only the successes are counted.
            _write(context, req, model, None, error=repr(exc))
            raise
        _write(context, req, model, result)
        return result


def _write(
    context: LogContext,
    req: LLMRequest,
    model: str,
    result: LLMResult | None,
    error: str | None = None,
) -> None:
    try:
        with session_scope() as session:
            session.add(
                Generation(
                    project_id=context.project_id,
                    target_type=context.target_type,
                    target_id=context.target_id,
                    attempt_no=context.attempt_no,
                    provider=result.provider if result else "unknown",
                    model=result.model if result else model,
                    params_json={
                        "task": str(req.task),
                        "effort": req.effort,
                        "max_tokens": req.max_tokens,
                        "schema_enforced": req.json_schema is not None,
                    },
                    prompt_text=req.user[:20000],
                    prompt_sections_json={"system": (req.system or "")[:4000]},
                    skill_versions_json=context.skill_versions or {},
                    outputs_json=(
                        [{"text": result.text[:20000]}] if result and result.text else []
                    ),
                    provider_job_id=_generation_id(result),
                    raw_response_json={} if error else _usage(result),
                    cost_usd=result.cost_usd if result else None,
                    latency_ms=result.latency_ms if result else None,
                    tokens_in=result.tokens_in if result else None,
                    tokens_out=result.tokens_out if result else None,
                    status=(
                        GenerationStatus.FAILED if error else GenerationStatus.SUCCEEDED
                    ),
                    error=error[:4000] if error else None,
                    created_by_kind=ActorKind.AGENT,
                    created_by_agent=context.agent,
                )
            )
    except Exception as exc:  # noqa: BLE001
        # Logging must never take down the work it is recording — but a silent
        # logging failure means the log is quietly wrong, which is worse than
        # loud. Raised to error so it cannot be mistaken for noise.
        log.error(
            "generation_log.write_failed",
            agent=context.agent,
            error=str(exc)[:300],
            note="this call is missing from the generation log",
        )


def _generation_id(result: LLMResult | None) -> str | None:
    if result is None:
        return None
    return (result.raw_response or {}).get("id")


def _usage(result: LLMResult | None) -> dict:
    if result is None:
        return {}
    return {
        "usage": (result.raw_response or {}).get("usage", {}),
        "stop_reason": result.stop_reason,
    }


async def backfill_costs(limit: int = 500) -> int:
    """Fill in cost for rows the provider did not price inline.

    OpenRouter reports cost asynchronously, so a call often returns before its
    price is known. The generation id is always present, and the per-generation
    endpoint resolves it with an ordinary key — no management key needed.
    """
    settings = get_settings()
    with session_scope() as session:
        pending = [
            (g.id, g.provider_job_id)
            for g in session.scalars(
                select(Generation)
                .where(
                    Generation.cost_usd.is_(None),
                    Generation.provider_job_id.is_not(None),
                    Generation.status == GenerationStatus.SUCCEEDED,
                )
                .limit(limit)
            )
        ]

    if not pending:
        return 0

    filled = 0
    async with httpx.AsyncClient(timeout=30) as client:
        for row_id, generation_id in pending:
            try:
                response = await client.get(
                    "https://openrouter.ai/api/v1/generation",
                    params={"id": generation_id},
                    headers={"Authorization": f"Bearer {settings.openrouter_api_key}"},
                )
                if response.status_code != 200:
                    continue
                data = response.json().get("data", {})
                cost = data.get("total_cost")
                if cost is None:
                    continue
                with session_scope() as session:
                    row = session.get(Generation, row_id)
                    if row is not None:
                        row.cost_usd = float(cost)
                filled += 1
            except Exception:  # noqa: BLE001, S112
                continue

    log.info("generation_log.backfilled", filled=filled, attempted=len(pending))
    return filled

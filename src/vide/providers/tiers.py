"""Model tiers.

A tier maps task types to concrete models. Projects carry a tier name, so
"cheaper models for low-budget projects, better models for high-paying ones" is
a column on ``projects``, not a branch in the pipeline.

Routing rule: a task whose entry names a provider prefix gets that provider;
everything else goes to OpenRouter. When ``ANTHROPIC_API_KEY`` is absent, the
native Claude routes degrade to the same models over OpenRouter rather than
failing — the pipeline still runs, it just loses schema enforcement, prompt
caching and batch on those calls.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import structlog

from vide.config import get_settings
from vide.providers.anthropic_direct import AnthropicProvider
from vide.providers.base import LLMProvider, TaskType
from vide.providers.logged import LogContext, LoggedLLMProvider
from vide.providers.openrouter import OpenRouterProvider

log = structlog.get_logger(__name__)

#: How a native Claude model is spelled on OpenRouter, for the fallback path.
_OPENROUTER_ALIAS = {
    "claude-opus-5": "anthropic/claude-opus-5",
    "claude-sonnet-5": "anthropic/claude-sonnet-5",
    "claude-haiku-4-5": "anthropic/claude-haiku-4.5",
}


@dataclass(slots=True)
class Tier:
    name: str
    #: task type -> "provider:model"
    routes: dict[TaskType, str] = field(default_factory=dict)
    #: Default thinking effort for LLM tasks in this tier.
    effort: str = "medium"


#: 's tier table. Extraction tasks are schema-critical, so both tiers
#: route them to a model that can enforce a schema rather than be asked for one.
TIERS: dict[str, Tier] = {
    "standard": Tier(
        name="standard",
        effort="medium",
        routes={
            TaskType.LLM_WRITER: "anthropic:claude-sonnet-5",
            TaskType.LLM_REVIEWER_VISION: "anthropic:claude-sonnet-5",
            TaskType.EMBEDDING: "openrouter:openai/text-embedding-3-small",
        },
    ),
    "premium": Tier(
        name="premium",
        effort="high",
        routes={
            TaskType.LLM_WRITER: "anthropic:claude-opus-5",
            TaskType.LLM_REVIEWER_VISION: "anthropic:claude-opus-5",
            TaskType.EMBEDDING: "openrouter:openai/text-embedding-3-large",
        },
    ),
}


class ProviderRegistry:
    """Resolves (tier, task) to a provider instance plus a model id."""

    def __init__(self) -> None:
        self._llm: dict[str, LLMProvider] = {}
        self._settings = get_settings()

    def _provider(self, name: str) -> LLMProvider:
        if name not in self._llm:
            if name == "anthropic":
                self._llm[name] = AnthropicProvider()
            elif name == "openrouter":
                self._llm[name] = OpenRouterProvider()
            else:
                raise KeyError(f"unknown provider {name!r}")
        return self._llm[name]

    def resolve(
        self,
        tier_name: str,
        task: TaskType,
        *,
        log_context: LogContext | None = None,
    ) -> tuple[LLMProvider, str]:
        tier = TIERS.get(tier_name)
        if tier is None:
            raise KeyError(f"unknown model tier {tier_name!r}")
        route = tier.routes.get(task)
        if route is None:
            raise KeyError(f"tier {tier_name!r} has no route for task {task}")

        provider_name, _, model = route.partition(":")

        if provider_name == "anthropic" and not self._settings.anthropic_api_key:
            fallback = _OPENROUTER_ALIAS.get(model)
            if fallback is None:
                raise KeyError(
                    f"{model} has no OpenRouter alias and ANTHROPIC_API_KEY is unset"
                )
            log.info(
                "provider.fallback",
                task=str(task),
                model=model,
                reason="ANTHROPIC_API_KEY unset",
                note="schema enforcement, prompt caching and batch unavailable on this route",
            )
            provider = self._provider("openrouter")
            if log_context is not None:
                provider = LoggedLLMProvider(provider, log_context)
            return provider, fallback

        provider = self._provider(provider_name)
        if log_context is not None:
            provider = LoggedLLMProvider(provider, log_context)
        return provider, model

    def effort_for(self, tier_name: str) -> str:
        tier = TIERS.get(tier_name)
        return tier.effort if tier else "medium"


_registry: ProviderRegistry | None = None


def get_registry() -> ProviderRegistry:
    global _registry
    if _registry is None:
        _registry = ProviderRegistry()
    return _registry

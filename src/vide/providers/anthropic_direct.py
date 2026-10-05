"""Anthropic adapter, via the official SDK.

Used when a tier resolves to a Claude model. It exists alongside the OpenRouter
adapter because three things this pipeline depends on are only reachable on the
native API:

* **Schema-guaranteed output.** ``output_config.format`` constrains the response
  to a JSON Schema. requires the breakdown to be schema-enforced with no
  prose — asking a model nicely for JSON is not the same guarantee.
* **Prompt caching.** Every agent pastes a skill document into its prefix on
  every call. Across 70 scenes that prefix is identical each time, so
  caching it turns the repeated part into a cache read.
* **Batch.** Per-scene breakdown is a fan-out of independent calls with no
  latency requirement — the shape the Batches API exists for, at half price.

Routing between this and OpenRouter is a tier config change, not a code change.
"""

from __future__ import annotations

import json
import time
from typing import Any

import anthropic
import structlog

from vide.config import get_settings
from vide.providers.base import (
    ImageInput,
    LLMRequest,
    LLMResult,
    ModelCaps,
    ProviderError,
    SchemaViolation,
)

log = structlog.get_logger(__name__)

#: Published rates per million tokens, for cost capture on the generation row.
#: Only used for reporting — never for routing decisions.
_PRICING: dict[str, tuple[float, float]] = {
    "claude-opus-5": (5.00, 25.00),
    "claude-sonnet-5": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
}

_VISION_MODELS = {"claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5"}


class AnthropicProvider:
    """LLMProvider backed by the official ``anthropic`` SDK."""

    name = "anthropic"

    def __init__(self, api_key: str | None = None) -> None:
        settings = get_settings()
        key = api_key or settings.anthropic_api_key
        if not key:
            raise ProviderError(
                "ANTHROPIC_API_KEY is not set; route this task through OpenRouter "
                "or add the key to .env",
                provider=self.name,
            )
        self._client = anthropic.AsyncAnthropic(api_key=key)

    # -- capabilities ---------------------------------------------------------

    def capabilities(self, model: str) -> ModelCaps:
        pricing = _PRICING.get(model, (None, None))
        return ModelCaps(
            model=model,
            context_window=1_000_000 if model != "claude-haiku-4-5" else 200_000,
            max_output_tokens=128_000 if model != "claude-haiku-4-5" else 64_000,
            supports_vision=model in _VISION_MODELS,
            supports_json_schema=True,
            supports_thinking=True,
            supports_prompt_cache=True,
            supports_batch=True,
            input_cost_per_mtok=pricing[0],
            output_cost_per_mtok=pricing[1],
        )

    # -- completion -----------------------------------------------------------

    async def complete(self, req: LLMRequest, model: str) -> LLMResult:
        kwargs = self._build_kwargs(req, model)
        started = time.perf_counter()
        try:
            response = await self._client.messages.create(**kwargs)
        except anthropic.APIStatusError as exc:
            raise ProviderError(
                f"{exc.message}", provider=self.name, status=exc.status_code
            ) from exc
        except anthropic.APIConnectionError as exc:
            raise ProviderError(str(exc), provider=self.name) from exc
        latency_ms = int((time.perf_counter() - started) * 1000)

        # Safety classifiers can decline a request: HTTP 200 with an empty or
        # partial content array. Check before reading content.
        if response.stop_reason == "refusal":
            detail = getattr(response, "stop_details", None)
            category = getattr(detail, "category", None) if detail else None
            raise ProviderError(
                f"model declined the request (category={category})",
                provider=self.name,
            )

        text = "".join(b.text for b in response.content if b.type == "text")
        parsed = self._parse(text, req)
        usage = response.usage

        return LLMResult(
            text=text,
            parsed=parsed,
            model=response.model,
            provider=self.name,
            tokens_in=usage.input_tokens,
            tokens_out=usage.output_tokens,
            cache_read_tokens=getattr(usage, "cache_read_input_tokens", None),
            cache_write_tokens=getattr(usage, "cache_creation_input_tokens", None),
            cost_usd=self._cost(model, usage),
            latency_ms=latency_ms,
            stop_reason=response.stop_reason,
            raw_request=_redact(kwargs),
            raw_response=response.model_dump(mode="json"),
        )

    # -- request construction -------------------------------------------------

    def _build_kwargs(self, req: LLMRequest, model: str) -> dict[str, Any]:
        content: list[dict[str, Any]] = []
        for image in req.images:
            content.append(self._image_block(image))
            if image.label:
                content.append({"type": "text", "text": image.label})
        content.append({"type": "text", "text": req.user})

        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": req.max_tokens,
            "messages": [{"role": "user", "content": content}],
        }

        if req.system:
            block: dict[str, Any] = {"type": "text", "text": req.system}
            # The skill document and schema sit in the system prefix and are
            # byte-identical across a fan-out, so this is the cacheable span.
            if req.cache_system:
                block["cache_control"] = {"type": "ephemeral"}
            kwargs["system"] = [block]

        if req.json_schema is not None:
            kwargs["output_config"] = {
                "format": {
                    "type": "json_schema",
                    "name": req.schema_name,
                    "schema": req.json_schema,
                }
            }

        if req.thinking:
            kwargs["thinking"] = {"type": "adaptive"}
        if req.effort:
            kwargs.setdefault("output_config", {})["effort"] = req.effort

        return kwargs

    @staticmethod
    def _image_block(image: ImageInput) -> dict[str, Any]:
        if image.url:
            return {"type": "image", "source": {"type": "url", "url": image.url}}
        return {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": image.media_type,
                "data": image.base64_data,
            },
        }

    @staticmethod
    def _parse(text: str, req: LLMRequest) -> Any | None:
        if req.json_schema is None:
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            # output_config.format should make this unreachable. If it fires,
            # something changed upstream and the caller must know rather than
            # receive a silently empty record.
            raise SchemaViolation(
                f"schema-enforced call returned unparseable output: {exc}",
                provider="anthropic",
            ) from exc

    @staticmethod
    def _cost(model: str, usage: Any) -> float | None:
        rates = _PRICING.get(model)
        if not rates:
            return None
        in_rate, out_rate = rates
        cached_read = getattr(usage, "cache_read_input_tokens", 0) or 0
        cached_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
        return (
            usage.input_tokens * in_rate
            + cached_read * in_rate * 0.1
            + cached_write * in_rate * 1.25
            + usage.output_tokens * out_rate
        ) / 1_000_000


def _redact(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Strip nothing today, but the hook exists before credentials ever land here."""
    return kwargs

"""OpenRouter adapter.

The default LLM route. One credential reaches ~460 models, which is what keeps
the core model-agnostic (principle 12) and makes the per-project tier a config
value rather than an integration.

``response_format: json_schema`` passes through to ``anthropic/*`` routes and
holds — verified against a real scene extraction. What the OpenAI-compatible
surface does not carry is prompt-cache breakpoints, adaptive thinking, or the
Batches API. None of those block the pipeline; they are cost and latency levers
available on the native route if a project ever wants them.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import httpx
import structlog
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

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

#: Models known to honour `response_format: json_schema` strictly. Anything not
#: listed falls back to prompt-instructed JSON, which is best-effort — callers
#: that need a guarantee should check `capabilities().supports_json_schema`.
_STRICT_SCHEMA_PREFIXES = ("anthropic/", "openai/", "google/gemini")


class OpenRouterProvider:
    name = "openrouter"

    def __init__(self, api_key: str | None = None, base_url: str | None = None) -> None:
        settings = get_settings()
        self._api_key = api_key or settings.openrouter_api_key
        if not self._api_key:
            raise ProviderError("OPENROUTER_API_KEY is not set", provider=self.name)
        self._base_url = (base_url or settings.openrouter_base_url).rstrip("/")
        self._caps_cache: dict[str, ModelCaps] = {}

    # -- capabilities ---------------------------------------------------------

    def capabilities(self, model: str) -> ModelCaps:
        """Static view. `refresh_capabilities` fills this from the live catalogue."""
        if model in self._caps_cache:
            return self._caps_cache[model]
        return ModelCaps(
            model=model,
            supports_vision=True,
            supports_json_schema=model.startswith(_STRICT_SCHEMA_PREFIXES),
            supports_thinking=False,
            supports_prompt_cache=False,
            supports_batch=False,
        )

    async def refresh_capabilities(self) -> dict[str, ModelCaps]:
        """Pull the live model catalogue so tiers can be validated at startup.

        asks for provider limits to be verified during implementation rather
        than assumed; this is that check, run against the account's own catalogue.
        """
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(
                f"{self._base_url}/models", headers=self._headers()
            )
            response.raise_for_status()
        for entry in response.json().get("data", []):
            model_id = entry["id"]
            pricing = entry.get("pricing") or {}
            modalities = (entry.get("architecture") or {}).get("input_modalities") or []
            self._caps_cache[model_id] = ModelCaps(
                model=model_id,
                context_window=entry.get("context_length"),
                supports_vision="image" in modalities,
                supports_json_schema=model_id.startswith(_STRICT_SCHEMA_PREFIXES),
                input_cost_per_mtok=_per_mtok(pricing.get("prompt")),
                output_cost_per_mtok=_per_mtok(pricing.get("completion")),
            )
        return self._caps_cache

    # -- completion -----------------------------------------------------------

    @retry(
        retry=retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError)),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        stop=stop_after_attempt(4),
        reraise=True,
    )
    async def _post(self, payload: dict[str, Any]) -> httpx.Response:
        async with httpx.AsyncClient(timeout=300) as client:
            response = await client.post(
                f"{self._base_url}/chat/completions",
                headers=self._headers(),
                json=payload,
            )
            # 4xx other than 429 is a request problem — don't burn retries on
            # it. 402 is the exception: OpenRouter returns it when concurrent
            # requests exceed the in-flight credit budget, which clears on its
            # own. Wait the interval they ask for and let tenacity retry.
            if response.status_code == 402:
                retry_after = response.headers.get("Retry-After")
                delay = min(float(retry_after or 20), 120)
                log.warning("openrouter.in_flight_budget", sleeping=delay)
                await asyncio.sleep(delay)
                response.raise_for_status()
            if response.status_code >= 500 or response.status_code == 429:
                response.raise_for_status()
            return response

    async def complete(self, req: LLMRequest, model: str) -> LLMResult:
        payload = self._build_payload(req, model)
        started = time.perf_counter()
        response = await self._post(payload)
        latency_ms = int((time.perf_counter() - started) * 1000)

        if response.status_code >= 400:
            raise ProviderError(
                response.text[:500], provider=self.name, status=response.status_code
            )

        body = response.json()
        if "error" in body:
            raise ProviderError(str(body["error"]), provider=self.name)

        choice = body["choices"][0]
        text = choice["message"].get("content") or ""
        usage = body.get("usage") or {}

        return LLMResult(
            text=text,
            parsed=self._parse(text, req, model, choice.get("finish_reason")),
            model=body.get("model", model),
            provider=self.name,
            tokens_in=usage.get("prompt_tokens"),
            tokens_out=usage.get("completion_tokens"),
            cost_usd=usage.get("cost"),
            latency_ms=latency_ms,
            stop_reason=choice.get("finish_reason"),
            raw_request=payload,
            raw_response=body,
        )

    # -- request construction -------------------------------------------------

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    def _build_payload(self, req: LLMRequest, model: str) -> dict[str, Any]:
        content: list[dict[str, Any]] = []
        for image in req.images:
            content.append(self._image_part(image))
            if image.label:
                content.append({"type": "text", "text": image.label})
        content.append({"type": "text", "text": req.user})

        messages: list[dict[str, Any]] = []
        if req.system:
            messages.append({"role": "system", "content": req.system})
        messages.append({"role": "user", "content": content})

        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": req.max_tokens,
        }

        if req.json_schema is not None:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": req.schema_name,
                    "strict": True,
                    "schema": req.json_schema,
                },
            }
        return payload

    @staticmethod
    def _image_part(image: ImageInput) -> dict[str, Any]:
        url = image.url or f"data:{image.media_type};base64,{image.base64_data}"
        return {"type": "image_url", "image_url": {"url": url}}

    def _parse(
        self, text: str, req: LLMRequest, model: str, finish_reason: str | None = None
    ) -> Any | None:
        if req.json_schema is None:
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # Some routes wrap JSON in a fenced block despite response_format.
            stripped = text.strip().removeprefix("```json").removeprefix("```")
            stripped = stripped.removesuffix("```").strip()
            try:
                return json.loads(stripped)
            except json.JSONDecodeError as exc:
                # Say *why* it could not be parsed. "Unparseable" alone sends the
                # caller guessing; truncation and malformation need opposite fixes.
                cause = (
                    "output was truncated — raise max_tokens or send less input"
                    if finish_reason in ("length", "max_tokens")
                    else f"malformed JSON at position {exc.pos}"
                )
                raise SchemaViolation(
                    f"{model} returned unparseable output for a schema-enforced call: "
                    f"{cause} (finish_reason={finish_reason!r}, {len(text)} chars)",
                    provider=self.name,
                ) from exc


def _per_mtok(raw: str | float | None) -> float | None:
    """OpenRouter prices per token; the log stores per million."""
    if raw in (None, ""):
        return None
    try:
        return float(raw) * 1_000_000
    except (TypeError, ValueError):
        return None

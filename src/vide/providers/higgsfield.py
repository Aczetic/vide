"""Higgsfield adapter — images now, video at Stage 2.

Verified against the live API: auth is ``Authorization: Key <id>:<secret>``
(the credential is already in that form, pasted whole), submission is
``POST /<model-path>`` returning a request id, and progress is a poll against
``/requests/{id}/status``.

Two operational facts shape this adapter:

* Terminal states are ``completed``, ``failed``, ``nsfw`` and ``canceled``.
  ``nsfw`` is a content refusal, not an error — it needs its own branch so the
  review agent can record why rather than logging a generic failure.
* **Provider output expires after about seven days.** Anything worth keeping
  must be copied into R2 before then; the generation row holds both URLs and
  the R2 key is the durable one (see storage.yaml → ingest_policy).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import httpx
import structlog
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from vide.config import get_settings
from vide.providers.base import (
    GenerationResult,
    JobHandle,
    JobStatus,
    MediaOutput,
    ModelCaps,
    ProviderError,
)

log = structlog.get_logger(__name__)

BASE_URL = "https://api.higgsfield.ai"

#: Model path per task. Only paths confirmed against the published docs are
#: listed; anything else must be verified before it is added.
MODEL_PATHS: dict[str, str] = {
    "soul-v2-standard": "/higgsfield-ai/soul/v2/standard",
    "seedance-2.0-t2v": "/bytedance/seedance-2.0/text-to-video",
    "genjutsu-motion-transfer": "/higgsfield/genjutsu/motion-transfer/v1.0",
}

TERMINAL_STATES = {"completed", "failed", "nsfw", "canceled"}

#: Verified against the API: anything else is rejected with a 4xx. "2k" and "4k"
#: are not accepted even though other providers use them.
RESOLUTIONS = ("720p", "1080p")


@dataclass(slots=True)
class MediaRequest:
    """A generation request in provider-neutral terms."""

    prompt: str
    #: Named reference elements, stored with their media rather than bare URLs
    #: so a generation can be replayed exactly (Appendix C).
    reference_urls: list[str] = field(default_factory=list)
    aspect_ratio: str = "9:16"
    resolution: str = "1080p"
    seed: int | None = None
    duration: int | None = None  # Stage 2
    generate_audio: bool = False
    extra: dict[str, Any] = field(default_factory=dict)


class ContentRefused(ProviderError):
    """The provider declined on content grounds (``nsfw``), not a failure."""


class HiggsfieldProvider:
    """MediaProvider: submit / poll / fetch_result, per """

    name = "higgsfield"

    def __init__(self, api_key: str | None = None) -> None:
        key = api_key or get_settings().higgsfield_api_key
        if not key:
            raise ProviderError("HIGGSFIELD_API_KEY is not set", provider=self.name)
        self._headers = {
            "Authorization": f"Key {key.strip()}",
            "Content-Type": "application/json",
        }

    # -- capabilities ---------------------------------------------------------

    def capabilities(self, model: str) -> ModelCaps:
        is_video = "seedance" in model or "video" in model
        return ModelCaps(
            model=model,
            supports_vision=False,
            supports_edit="soul" not in model,
            # The reference budget decides how many named characters can share a
            # shot — surfaced to prompt writers as a constraint.
            max_reference_images=9,
            aspects=("9:16", "16:9", "1:1", "21:9", "4:3", "3:4"),
            resolutions=RESOLUTIONS,
        ) if not is_video else ModelCaps(
            model=model,
            max_reference_images=9,
            aspects=("9:16", "16:9", "21:9"),
            resolutions=RESOLUTIONS,
        )

    # -- lifecycle ------------------------------------------------------------

    @retry(
        retry=retry_if_exception_type(httpx.TransportError),
        wait=wait_exponential(multiplier=1, min=2, max=20),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    async def submit(self, req: MediaRequest, model: str) -> JobHandle:
        path = MODEL_PATHS.get(model)
        if path is None:
            known = ", ".join(sorted(MODEL_PATHS))
            raise ProviderError(
                f"no verified endpoint for model {model!r}; known: {known}",
                provider=self.name,
            )

        payload: dict[str, Any] = {
            "prompt": req.prompt,
            "aspect_ratio": req.aspect_ratio,
            "resolution": req.resolution,
            **req.extra,
        }
        if req.reference_urls:
            payload["reference_images"] = req.reference_urls
        if req.seed is not None:
            payload["seed"] = req.seed
        if req.duration is not None:
            payload["duration"] = req.duration
            payload["generate_audio"] = req.generate_audio

        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                f"{BASE_URL}{path}", headers=self._headers, json=payload
            )
        if response.status_code >= 400:
            raise ProviderError(
                response.text[:500], provider=self.name, status=response.status_code
            )

        body = response.json()
        request_id = body.get("request_id")
        if not request_id:
            raise ProviderError(f"no request_id in response: {body}", provider=self.name)

        log.info("higgsfield.submitted", model=model, request_id=request_id)
        return JobHandle(
            provider=self.name,
            job_id=request_id,
            model=model,
            submitted_at=time.time(),
        )

    async def poll(self, handle: JobHandle) -> JobStatus:
        body = await self._status(handle.job_id)
        state = body.get("status", "unknown")
        if state == "nsfw":
            return JobStatus(state="failed", error="content refused (nsfw)")
        if state == "failed":
            return JobStatus(state="failed", error=body.get("error") or "provider failed")
        if state in ("completed", "canceled"):
            return JobStatus(state="succeeded" if state == "completed" else "cancelled")
        return JobStatus(state="running", progress=body.get("progress"))

    async def fetch_result(self, handle: JobHandle) -> GenerationResult:
        body = await self._status(handle.job_id)
        state = body.get("status")

        if state == "nsfw":
            raise ContentRefused(
                "provider refused the request on content grounds",
                provider=self.name,
            )
        if state != "completed":
            raise ProviderError(
                f"request {handle.job_id} is {state}, not completed",
                provider=self.name,
            )

        outputs = [
            MediaOutput(
                url=item["url"],
                thumbnail_url=item.get("thumbnail_url"),
                width=item.get("width"),
                height=item.get("height"),
                content_type=item.get("content_type"),
            )
            for item in _media_items(body)
        ]
        return GenerationResult(
            outputs=outputs,
            latency_ms=int((time.time() - handle.submitted_at) * 1000),
            raw_response=body,
        )

    async def cancel(self, handle: JobHandle) -> None:
        async with httpx.AsyncClient(timeout=30) as client:
            await client.post(
                f"{BASE_URL}/requests/{handle.job_id}/cancel", headers=self._headers
            )

    # -- uploads --------------------------------------------------------------

    async def upload_reference(self, data: bytes, content_type: str) -> str:
        """Put a reference image where the provider can read it.

        Returns the public URL to pass back in ``reference_urls``.
        """
        async with httpx.AsyncClient(timeout=120) as client:
            signed = await client.post(
                f"{BASE_URL}/files/generate-upload-url",
                headers=self._headers,
                json={"content_type": content_type},
            )
            signed.raise_for_status()
            slot = signed.json()

            put = await client.put(
                slot["upload_url"], content=data, headers=slot.get("upload_headers", {})
            )
            put.raise_for_status()
        return slot["public_url"]

    # -- internals ------------------------------------------------------------

    async def _status(self, request_id: str) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.get(
                f"{BASE_URL}/requests/{request_id}/status", headers=self._headers
            )
        if response.status_code >= 400:
            raise ProviderError(
                response.text[:500], provider=self.name, status=response.status_code
            )
        return response.json()


def _media_items(body: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalise the two output shapes: ``images[]`` and a single ``video``."""
    if isinstance(body.get("images"), list):
        return [i for i in body["images"] if isinstance(i, dict) and i.get("url")]
    video = body.get("video")
    if isinstance(video, dict) and video.get("url"):
        return [video]
    return []

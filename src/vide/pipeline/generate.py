"""Image generation: submit, poll, store, log.

N candidates per asset in parallel (default 4), because the client picks one at
G2 and a single option is not a choice.

Every call writes a ``generations`` row before it is submitted and updates it
when it lands, so a crash mid-flight leaves a record of what was attempted
rather than a silent gap. Output is copied into R2 because the provider expires
its own URLs after about a week — the provider URL is for immediate display,
the R2 key is the durable one.
"""

from __future__ import annotations

import asyncio
import struct
import uuid
from dataclasses import dataclass, field

import httpx
import structlog
from sqlalchemy import select

from vide.models.asset import Asset, AssetVersion
from vide.models.enums import ActorKind, ElementStatus, GenerationStatus
from vide.models.generation import Generation
from vide.providers import ContentRefused, MediaRequest, ProviderError
from vide.providers.higgsfield import HiggsfieldProvider
from vide.storage import get_client, get_layout

log = structlog.get_logger(__name__)

#: How long to wait for one generation before giving up, and how often to ask.
POLL_INTERVAL_S = 5
POLL_TIMEOUT_S = 600


@dataclass(slots=True)
class Candidate:
    generation_id: uuid.UUID
    url: str | None = None
    r2_key: str | None = None
    width: int | None = None
    height: int | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.url is not None


@dataclass(slots=True)
class GenerationBatch:
    asset_id: uuid.UUID
    candidates: list[Candidate] = field(default_factory=list)
    cost_usd: float = 0.0

    @property
    def succeeded(self) -> list[Candidate]:
        return [c for c in self.candidates if c.ok]


def _png_size(data: bytes) -> tuple[int | None, int | None]:
    """Read width and height out of the PNG header.

    The provider does not report dimensions, and the justified grid needs them
    before the image loads or every scroll reflows. The IHDR chunk puts
    them at a fixed offset, so this costs nothing.
    """
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n":
        return None, None
    width, height = struct.unpack(">II", data[16:24])
    return width, height


def _store_in_r2(data: bytes, project_id: uuid.UUID, entity_type: str, **parts) -> str:
    """Copy provider output into R2 and return the key."""
    layout = get_layout()
    prefix = {
        "character": "character_assets",
        "location": "location_assets",
        "prop": "prop_assets",
        "screen_insert": "prop_assets",
        "vehicle": "prop_assets",
    }.get(entity_type, "prop_assets")

    key = layout.key(prefix, project_id=str(project_id), **parts)
    get_client().put_object(
        Bucket=layout.bucket, Key=key, Body=data, ContentType="image/png"
    )
    return key


async def _one_candidate(
    provider: HiggsfieldProvider,
    session_factory,
    *,
    project_id: uuid.UUID,
    asset: Asset,
    prompt: str,
    prompt_sections: dict,
    model: str,
    aspect: str,
    resolution: str,
    attempt: int,
    skill_versions: dict,
    index: int,
) -> Candidate:
    # Record the attempt before it leaves, so a crash mid-flight is visible.
    with session_factory() as session:
        generation = Generation(
            project_id=project_id,
            target_type="asset",
            target_id=asset.id,
            attempt_no=attempt,
            provider=provider.name,
            model=model,
            params_json={"aspect_ratio": aspect, "candidate": index},
            prompt_text=prompt,
            prompt_sections_json=prompt_sections,
            skill_versions_json=skill_versions,
            status=GenerationStatus.QUEUED,
            created_by_kind=ActorKind.AGENT,
            created_by_agent="sheet-generator",
        )
        session.add(generation)
        session.flush()
        generation_id = generation.id

    candidate = Candidate(generation_id=generation_id)

    try:
        handle = await provider.submit(
            MediaRequest(prompt=prompt, aspect_ratio=aspect, resolution=resolution), model
        )
        with session_factory() as session:
            row = session.get(Generation, generation_id)
            row.provider_job_id = handle.job_id
            row.status = GenerationStatus.RUNNING

        waited = 0
        while waited < POLL_TIMEOUT_S:
            await asyncio.sleep(POLL_INTERVAL_S)
            waited += POLL_INTERVAL_S
            status = await provider.poll(handle)
            if status.state in ("succeeded", "failed", "cancelled"):
                break
        else:
            raise ProviderError("timed out waiting for the provider", provider=provider.name)

        if status.state != "succeeded":
            raise ProviderError(status.error or "generation failed", provider=provider.name)

        outcome = await provider.fetch_result(handle)
        if not outcome.outputs:
            raise ProviderError("provider returned no images", provider=provider.name)

        output = outcome.outputs[0]
        candidate.url = output.url
        candidate.width = output.width
        candidate.height = output.height

        # Copy it somewhere durable before the provider expires it.
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.get(output.url)
            response.raise_for_status()
            # The provider leaves these null; read them from the file itself.
            if candidate.width is None:
                candidate.width, candidate.height = _png_size(response.content)
            candidate.r2_key = await asyncio.to_thread(
                _store_in_r2,
                response.content,
                project_id,
                asset.entity_type,
                entity_id=str(asset.entity_id),
                asset_id=str(asset.id),
                version=1,
                filename=f"candidate-{index}.png",
            )

        with session_factory() as session:
            row = session.get(Generation, generation_id)
            row.status = GenerationStatus.SUCCEEDED
            row.outputs_json = [
                {
                    "url": output.url,
                    "r2_key": candidate.r2_key,
                    "width": candidate.width,
                    "height": candidate.height,
                    "content_type": "image/png",
                }
            ]
            row.raw_response_json = outcome.raw_response
            row.latency_ms = outcome.latency_ms

    except ContentRefused as exc:
        candidate.error = f"content refused: {exc}"
    except Exception as exc:  # noqa: BLE001 — the failure belongs on the row
        candidate.error = repr(exc)

    if candidate.error:
        with session_factory() as session:
            row = session.get(Generation, generation_id)
            if row is not None:
                row.status = GenerationStatus.FAILED
                row.error = candidate.error[:4000]
        log.warning("generate.failed", asset=str(asset.id), error=candidate.error[:200])

    return candidate


async def generate_candidates(
    session_factory,
    *,
    asset_id: uuid.UUID,
    project_id: uuid.UUID,
    prompt: str,
    prompt_sections: dict,
    model: str = "soul-v2-standard",
    aspect: str = "16:9",
    # The provider accepts 720p or 1080p only; 2k is rejected outright.
    resolution: str = "1080p",
    count: int = 4,
    attempt: int = 1,
    skill_versions: dict | None = None,
) -> GenerationBatch:
    """Generate N candidates for one asset, in parallel."""
    provider = HiggsfieldProvider()

    with session_factory() as session:
        asset = session.get(Asset, asset_id)
        if asset is None:
            raise LookupError(f"asset {asset_id} not found")
        session.expunge(asset)

    with session_factory() as session:
        row = session.get(Asset, asset_id)
        row.status = ElementStatus.GENERATING

    candidates = await asyncio.gather(
        *(
            _one_candidate(
                provider,
                session_factory,
                project_id=project_id,
                asset=asset,
                prompt=prompt,
                prompt_sections=prompt_sections,
                model=model,
                aspect=aspect,
                resolution=resolution,
                attempt=attempt,
                skill_versions=skill_versions or {},
                index=i,
            )
            for i in range(count)
        )
    )

    batch = GenerationBatch(asset_id=asset_id, candidates=list(candidates))

    # Record the version even when some candidates failed — a partial set is
    # still something a reviewer can pick from.
    with session_factory() as session:
        row = session.get(Asset, asset_id)
        if batch.succeeded:
            # Versions are immutable (principle 10): a re-run is a new version,
            # never an overwrite of the one a reviewer may already have seen.
            existing = session.scalars(
                select(AssetVersion.version).where(AssetVersion.asset_id == asset_id)
            ).all()
            next_version = max(existing, default=0) + 1
            session.add(
                AssetVersion(
                    asset_id=asset_id,
                    version=next_version,
                    images_json=[
                        {
                            "url": c.url,
                            "r2_key": c.r2_key,
                            "width": c.width,
                            "height": c.height,
                            "generation_id": str(c.generation_id),
                        }
                        for c in batch.succeeded
                    ],
                )
            )
            row.status = ElementStatus.AGENT_REVIEW
        else:
            row.status = ElementStatus.ESCALATED
            row.stale_reason = "every candidate failed to generate"

    log.info(
        "generate.batch",
        asset=str(asset_id),
        requested=count,
        succeeded=len(batch.succeeded),
    )
    return batch

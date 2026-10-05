"""Provider layer contracts.

Two protocols, because the two kinds of work have different shapes:

``LLMProvider``  — text in, text or structured JSON out. Fast, synchronous.
``MediaProvider`` — image and video generation. Slow and asynchronous, so it is
                    submit / poll / fetch as specifies.

Model-specific knowledge lives behind these interfaces and nowhere else
(principle 12). Core code asks for a task type and a project tier; the registry
resolves that to a provider and a model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable


class TaskType(StrEnum):
    """What a call is for. Tiers map these to concrete models."""

    LLM_WRITER = "llm_writer"
    LLM_REVIEWER_VISION = "llm_reviewer_vision"
    EMBEDDING = "embedding"
    CHAR_SHEET = "char_sheet"
    LOCATION_PLATE = "location_plate"
    PROP_SHEET = "prop_sheet"
    EDIT = "edit"
    TEXTURE = "texture"
    KEYFRAME = "keyframe"
    VIDEO = "video"  # Stage 2


@dataclass(slots=True)
class ImageInput:
    """An image passed to a vision call. Either a URL or inline base64."""

    url: str | None = None
    base64_data: str | None = None
    media_type: str = "image/png"
    #: What this image is, for the prompt — e.g. "approved master @char_CB_Kel_v9".
    label: str | None = None


@dataclass(slots=True)
class LLMRequest:
    task: TaskType
    #: Stable across calls in a batch, so it can be cached as a prefix.
    system: str | None = None
    user: str = ""
    images: list[ImageInput] = field(default_factory=list)
    #: A JSON Schema. When set, the provider must guarantee conforming output.
    #: requires the breakdown to be schema-enforced with no prose.
    json_schema: dict[str, Any] | None = None
    schema_name: str = "result"
    max_tokens: int = 8192
    #: Thinking depth. Providers that do not support it ignore the value.
    effort: str | None = None
    thinking: bool = True
    #: Ask the provider to cache the system prefix if it can.
    cache_system: bool = True
    model_override: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class LLMResult:
    text: str
    #: Populated when json_schema was set and the provider honoured it.
    parsed: Any | None = None
    model: str = ""
    provider: str = ""
    tokens_in: int | None = None
    tokens_out: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    cost_usd: float | None = None
    latency_ms: int | None = None
    stop_reason: str | None = None
    #: Kept verbatim so a generation can be replayed exactly.
    raw_request: dict[str, Any] = field(default_factory=dict)
    raw_response: dict[str, Any] = field(default_factory=dict)

    @property
    def schema_enforced(self) -> bool:
        """True when the provider guaranteed the shape rather than being asked nicely."""
        return self.parsed is not None


@dataclass(slots=True)
class ModelCaps:
    """What a model can do. Surfaced to prompt writers as constraints —
    the reference budget decides how many named characters can share a shot."""

    model: str
    context_window: int | None = None
    max_output_tokens: int | None = None
    supports_vision: bool = False
    supports_json_schema: bool = False
    supports_thinking: bool = False
    supports_prompt_cache: bool = False
    supports_batch: bool = False
    aspects: tuple[str, ...] = ()
    #: Resolution strings the provider accepts. Values outside this are 4xx.
    resolutions: tuple[str, ...] = ()
    max_reference_images: int | None = None
    supports_edit: bool = False
    input_cost_per_mtok: float | None = None
    output_cost_per_mtok: float | None = None


@dataclass(slots=True)
class JobHandle:
    provider: str
    job_id: str
    model: str
    submitted_at: float


@dataclass(slots=True)
class JobStatus:
    state: str  # queued | running | succeeded | failed | cancelled
    progress: float | None = None
    error: str | None = None


@dataclass(slots=True)
class MediaOutput:
    url: str
    thumbnail_url: str | None = None
    #: Carried because the virtualised justified grid needs intrinsic size
    #: before the image loads.
    width: int | None = None
    height: int | None = None
    content_type: str | None = None


@dataclass(slots=True)
class GenerationResult:
    outputs: list[MediaOutput]
    cost_usd: float | None = None
    latency_ms: int | None = None
    raw_response: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class LLMProvider(Protocol):
    name: str

    async def complete(self, req: LLMRequest, model: str) -> LLMResult: ...

    def capabilities(self, model: str) -> ModelCaps: ...


@runtime_checkable
class MediaProvider(Protocol):
    """'s adapter interface. Providers are slow, so everything is async."""

    name: str

    async def submit(self, req: Any, model: str) -> JobHandle: ...

    async def poll(self, handle: JobHandle) -> JobStatus: ...

    async def fetch_result(self, handle: JobHandle) -> GenerationResult: ...

    def capabilities(self, model: str) -> ModelCaps: ...


class ProviderError(RuntimeError):
    """A provider call failed in a way worth recording on the generation row."""

    def __init__(self, message: str, *, provider: str, status: int | None = None):
        super().__init__(message)
        self.provider = provider
        self.status = status


class SchemaViolation(ProviderError):
    """The provider returned output that does not match the requested schema.

    Raised rather than papered over: says the breakdown is schema-enforced,
    so silently accepting prose would corrupt everything downstream.
    """

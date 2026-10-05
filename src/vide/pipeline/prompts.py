"""Image prompt writing.

Turns a character spec into a sheet prompt. The loaded skill document
carries the craft guidance; this module carries the assertions — patterns
that must or must not appear, checked before a prompt is ever sent.

The division matters: a skill instructs, and a model may ignore an
instruction. These are enforced. A prompt that breaks one is repaired
surgically, and rejected only if the repair also fails — spending four
generations to rediscover a known failure is waste.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import structlog

from vide.providers import LLMRequest, LogContext, SchemaViolation, TaskType, get_registry

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

# The two reference sources disagree about the word "studio". The image-prompt
# skill recommends "studio photographs" as the safe replacement for the phrase
# "character reference sheet"; the project brief says to avoid "studio" because
# the model draws an actual photo studio with stands in frame and bakes a key
# light that repeats in every later generation.
#
# Both are right about different things. "studio photograph" is a genre term —
# a kind of image. "studio backdrop", "studio lighting" and "in a studio" name a
# place and a light, which is the failure the brief describes. The genre term is
# allowed; the place and the light are not.
_FORBIDDEN: tuple[tuple[str, str], ...] = (
    (
        r"\bstudio\s+(backdrop|background|lighting|light|key|setup|set\b)"
        r"|\b(in|inside|at)\s+a?\s*studio\b|\bphoto\s*studio\b",
        "names a studio as a place or a light source — the model draws stands "
        "and equipment into frame and bakes a key light into every later shot",
    ),
    (r"\brim\s*light", "rim light is not permitted on a reference sheet"),
    (r"character reference sheet", "triggers an illustration style on a photoreal job"),
    (r"\bpainterly\b", "triggers an illustration style on a photoreal job"),
    (r"--ar\b|\b\d{1,2}:\d{1,2}\b|\b4k\b|\b2k\b", "aspect and resolution are provider parameters"),
    (r"\bholding\b|\bholds a\b|\bgripping\b", "hands stay empty; objects are separate assets"),
)

#: Structural requirements of Matched loosely on purpose — the check is
#: "did the prompt ask for this panel", not "did it use my preferred wording".
#: A prompt saying "facing the camera" has asked for a front view.
_REQUIRED: tuple[tuple[str, str], ...] = (
    (r"\bthree\b|\b3\b", "the sheet is three panels"),
    (r"\bfront\b|facing (the )?camera|frontal|head-?on", "a full body front panel"),
    (r"\bback\b|behind|rear|from the rear", "a full body back panel"),
    (r"grey|gray|neutral", "neutral grey background"),
    (r"portrait|close-?up|head and shoulders", "a close portrait panel"),
)


@dataclass(slots=True)
class SheetPrompt:
    character_id: str
    name: str
    variant_label: str
    prompt: str
    #: Kept as named sections so a fix patches one and leaves the rest
    #: byte-identical.
    sections: dict[str, str] = field(default_factory=dict)
    violations: list[str] = field(default_factory=list)
    #: Violations that were auto-repaired rather than fatal.
    repaired_from: list[str] = field(default_factory=list)
    model: str = ""
    cost_usd: float | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and not self.violations


PROMPT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "subject": {
            "type": "string",
            "description": "Who this is: age, build, ethnicity as type, face, hair, marks.",
        },
        "wardrobe": {
            "type": "string",
            "description": "The garment sentence, stated as consistent across all panels.",
        },
        "panels": {
            "type": "string",
            "description": (
                "The three panels in flowing prose — full body front, full body "
                "back, close three-quarter portrait. No CAPS block headers."
            ),
        },
        "lighting": {
            "type": "string",
            "description": (
                "Soft and directional, no hard shadows, no blown highlights. "
                "State the absence of equipment positively."
            ),
        },
        "tech": {
            "type": "string",
            "description": "Film stock / camera register. One line, not a stack.",
        },
        "full_prompt": {
            "type": "string",
            "description": (
                "The sections composed into one natural-prose prompt, 80-150 "
                "words of real signal, under 1500 characters."
            ),
        },
    },
    "required": ["subject", "wardrobe", "panels", "lighting", "tech", "full_prompt"],
    "additionalProperties": False,
}

#: Loaded from the skill registry rather than inlined, so it can be
#: versioned and pinned per project. Not included in this repository.
SYSTEM_PREAMBLE_SKILL = "character-sheet-prompt"
def check(prompt: str) -> list[str]:
    """Assert the rules that must hold regardless of what the model wrote."""
    violations = []
    lowered = prompt.lower()
    for pattern, reason in _FORBIDDEN:
        if re.search(pattern, lowered):
            match = re.search(pattern, lowered)
            violations.append(f"contains {match.group(0)!r} — {reason}")
    for pattern, reason in _REQUIRED:
        if not re.search(pattern, lowered):
            violations.append(f"missing: {reason}")
    if len(prompt) > 2000:
        violations.append(f"{len(prompt)} characters — past 2000 details start dropping")
    return violations


async def write_sheet_prompt(
    *,
    character_id: str,
    name: str,
    spec: dict[str, Any],
    image_skill: str,
    variant_label: str = "master sheet",
    smiling: bool = False,
    tier_name: str = "standard",
) -> SheetPrompt:
    registry = get_registry()
    provider, model = registry.resolve(

        tier_name,

        TaskType.LLM_WRITER,

        log_context=LogContext(agent="image-prompt-writer", target_type="asset"),

    )

    marks = ", ".join(spec.get("distinguishing_marks", [])) or "none noted"
    brief = (
        f"Build a three-panel character sheet prompt for {name}.\n\n"
        f"Age and build: {spec.get('age_build', 'unstated')}\n"
        f"Face and body lock: {spec.get('face_body_lock', 'unstated')}\n"
        f"Distinguishing marks: {marks}\n"
        f"Hair and grooming: {spec.get('hair_grooming', 'unstated')}\n"
        f"Signature wardrobe: {spec.get('signature_wardrobe', 'unstated')}\n\n"
        + (
            "This is the SMILING close-up variant. Otherwise identical to the "
            "neutral sheet — the model invents teeth and jaw behaviour the first "
            "time the character laughs unless both exist.\n"
            if smiling
            else ""
        )
    )

    request = LLMRequest(
        task=TaskType.LLM_WRITER,
        system=f"{_system(SYSTEM_PREAMBLE_SKILL)}\n\n--- IMAGE PROMPT SKILL ---\n\n{image_skill}",
        user=brief,
        json_schema=PROMPT_SCHEMA,
        schema_name="sheet_prompt",
        effort="low",
        # Generous, because thinking tokens count against this ceiling and the
        # skill in the system prompt is long. A tight budget here produces zero
        # characters of output and a truncation error, not a shorter prompt.
        max_tokens=16000,
    )

    result = SheetPrompt(
        character_id=character_id, name=name, variant_label=variant_label, prompt=""
    )
    try:
        response = await provider.complete(request, model)
    except SchemaViolation:
        # Retry once with more room before giving up on the character.
        request.max_tokens = 24000
        try:
            response = await provider.complete(request, model)
        except Exception as exc:  # noqa: BLE001
            result.error = f"failed twice: {exc}"
            return result
    except Exception as exc:  # noqa: BLE001
        result.error = repr(exc)
        return result

    parsed = response.parsed or {}
    result.prompt = parsed.get("full_prompt", "")
    result.sections = {k: v for k, v in parsed.items() if k != "full_prompt"}
    result.model = response.model
    result.cost_usd = response.cost_usd

    violations = check(result.prompt)
    if violations:
        # One stray phrase should not cost a character its sheet. Repair the
        # prompt and re-check; only give up if the repair also fails. The
        # violations are still recorded so the pattern is visible in the log.
        log.warning("prompt.repairing", character=name, violations=violations)
        repaired = _repair(result.prompt)
        if not check(repaired):
            result.prompt = repaired
            result.repaired_from = violations
            violations = []
        result.violations = violations

    return result


#: Literal substitutions for the phrases the writer reaches for despite being
#: told not to. Each preserves the intent and drops the failure.
_REPAIRS: tuple[tuple[str, str], ...] = (
    (r"\bstudio\s+(?:lighting|light|key)\b", "soft directional light"),
    (r"\bstudio\s+(?:backdrop|background)\b", "flat neutral mid-grey backdrop"),
    (r"\bstudio\s+(?:setup|set)\b", "neutral backdrop"),
    (r"\b(?:in|inside|at)\s+a?\s*studio\b", "against a neutral backdrop"),
    (r"\bphoto\s*studio\b", "neutral backdrop"),
    (r"\brim\s*light(?:ing)?\b", "soft fill"),
    (r"character reference sheet", "film character sheet"),
    (r"\bpainterly\b", "photographic"),
    (r"\bholding\b", "with empty hands near"),
    (r"\bgripping\b", "with empty hands near"),
    (r"\b\d{1,2}:\d{1,2}\b", ""),
    (r"\b[24]k\b", ""),
)


def _repair(prompt: str) -> str:
    """Rewrite banned phrases in place, preserving everything else.

    Surgical by design: this is the same discipline as a prompt patch — change
    the failing fragment, leave the rest byte-identical.
    """
    out = prompt
    for pattern, replacement in _REPAIRS:
        out = re.sub(pattern, replacement, out, flags=re.IGNORECASE)
    # A substitution can leave its own adjective doubled ("soft soft directional
    # light") when the original already carried one.
    out = re.sub(r"\b(\w+)(\s+\1\b)+", r"\1", out, flags=re.IGNORECASE)
    return re.sub(r"\s{2,}", " ", out).strip()

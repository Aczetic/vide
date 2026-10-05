"""Split normalised text into episodes, scenes and dialogue.

Deterministic, not LLM. The structural split is a parsing problem with exact
answers, and getting it from a regex means it is free, instant, reproducible,
and never hallucinates a scene. The LLM's judgement is spent on the breakdown,
where it is actually needed.

Every parsed item keeps a source span back to the line it came from, so a
reviewer can click through to the original text.

Real variance this handles, measured against the reference screenplay:

* Headers as ``SCENE 12 –``, ``SCENE 12:`` and ``SC 1 –``
* Scene numbers with letter suffixes: ``2A``, ``2/A``, ``1A``, ``1/A``
* Headers with no character list at all
* Two scenes sharing a number, joined by ``INT CUT`` — an intercut pair that
  plays simultaneously, not a duplicate
* Transition markers in five spellings, plus time jumps like ``ONE WEEK LATER``
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import structlog

log = structlog.get_logger(__name__)

EN_DASH = "–"

_EPISODE = re.compile(r"^EPISODE\s+(\d+)\s*$", re.IGNORECASE)

_SCENE_HEADER = re.compile(
    r"^(?:SC|SCENE)\s*"
    r"(?P<number>\d+)"
    r"(?P<suffix>\s*/?\s*[A-Za-z])?"          # 2A, 2/A, 1/A
    r"\s*[:–—-]\s*"
    r"(?P<body>.+)$",
    re.IGNORECASE,
)

#: `NAME (cue): line` — the speaker is uppercase, the cue is optional.
_DIALOGUE = re.compile(
    r"^(?P<speaker>[A-Z][A-Z0-9 .'’/-]{1,40}?)"
    r"(?:\s*\((?P<cue>[^)]*)\))?"
    r"\s*:\s*"
    r"(?P<line>.+)$"
)

#: A cue segment reads as stage action when it opens with a verb form.
_VERBAL = re.compile(r"^\w+(?:s|ing|ed)\b", re.IGNORECASE)

#: Markers that end a scene or mark a jump. Matched case-insensitively.
_TRANSITIONS = {
    "freeze", "cut to", "cut to:", "intercut", "inter cut", "int cut",
    "dissolve to", "smash cut", "the end", "fade out", "fade in",
}

_TIME_JUMP = re.compile(
    r"^(?:(?:a\s+)?few\s+(?:years?|days?|weeks?|months?)\s+later"
    r"|one\s+(?:year|day|week|month)\s+later"
    r"|\d+\s+(?:years?|days?|weeks?|months?)\s+later"
    r"|next\s+(?:morning|day|week)"
    r"|later\s+that\s+(?:day|night|evening))",
    re.IGNORECASE,
)

#: The combined form is written three ways — INT./EXT., EXT/INT. and EXT–INT.
#: The en-dash spelling matters: en dash is also the field separator, so this
#: has to be recognised before the heading is split or the token is torn in two.
_INT_EXT = re.compile(
    rf"^(INT\.?\s*[/{EN_DASH}]\s*EXT\.?|EXT\.?\s*[/{EN_DASH}]\s*INT\.?|INT\.?|EXT\.?)\s*(.*)$",
    re.IGNORECASE,
)

_KNOWN_TIMES = {
    "DAY", "NIGHT", "MORNING", "EVENING", "DAWN", "DUSK", "AFTERNOON",
    "CONTINUOUS", "LATER", "SAME TIME",
}


@dataclass(slots=True)
class ParsedDialogue:
    order: int
    speaker_raw: str
    line: str
    delivery_raw: str | None
    delivery_action: str | None
    delivery_emotion: str | None
    line_no: int


@dataclass(slots=True)
class ParsedScene:
    episode_number: int
    number: int
    number_label: str
    heading_raw: str
    int_ext: str | None
    location_raw: str | None
    sub_area_raw: str | None
    time_of_day: str | None
    characters_raw: list[str]
    action_lines: list[str]
    dialogue: list[ParsedDialogue]
    transition_out: str | None
    #: A time-jump marker sitting between the previous scene and this one, e.g.
    #: "ONE WEEK LATER". Attached to the scene it applies to — the one *after*
    #: the jump — because that is the scene whose story-day moves.
    time_jump_before: str | None
    #: Scenes joined by INT CUT / INTERCUT share this and play simultaneously.
    intercut_group: str | None
    start_line: int
    end_line: int
    gaps: list[str] = field(default_factory=list)
    #: Unique across the screenplay. Episode and scene number alone are NOT
    #: unique: an intercut pair shares a number, so keying on them collapses two
    #: different scenes into one and misattributes every location, character and
    #: costume in the second. A suffix disambiguates the collision.
    uid: str = ""

    @property
    def action_text(self) -> str:
        return "\n".join(self.action_lines)

    @property
    def body_text(self) -> str:
        """Scene text in original order, for the breakdown agent to read."""
        return "\n".join(
            [self.heading_raw]
            + self.action_lines
            + [f"{d.speaker_raw}: {d.line}" for d in self.dialogue]
        )


@dataclass(slots=True)
class ParsedEpisode:
    number: int
    scenes: list[ParsedScene]
    start_line: int
    end_line: int


@dataclass(slots=True)
class ParsedScreenplay:
    episodes: list[ParsedEpisode]
    gaps: list[str] = field(default_factory=list)

    @property
    def scenes(self) -> list[ParsedScene]:
        return [s for e in self.episodes for s in e.scenes]

    @property
    def stats(self) -> dict[str, int]:
        return {
            "episodes": len(self.episodes),
            "scenes": len(self.scenes),
            "dialogue_lines": sum(len(s.dialogue) for s in self.scenes),
            "gaps": len(self.gaps) + sum(len(s.gaps) for s in self.scenes),
        }


def _split_cue(cue: str | None) -> tuple[str | None, str | None]:
    """Separate a delivery cue into stage action and emotion.

    ``(Snatches the bread, Dismissive)`` is an action plus an emotion; they
    drive different things — the action is blocking, the emotion is
    performance — so they are stored apart. ``(Hurt, Confused)`` is two
    emotions and yields no action. The raw string is always preserved, so a
    wrong split costs nothing downstream.
    """
    if not cue:
        return None, None
    segments = [s.strip() for s in cue.split(",") if s.strip()]
    if not segments:
        return None, None
    if len(segments) == 1:
        return None, segments[0]
    if _VERBAL.match(segments[0]):
        return ", ".join(segments[:-1]), segments[-1]
    return None, ", ".join(segments)


@dataclass(slots=True)
class ParsedHeading:
    int_ext: str | None
    location_raw: str | None
    #: Present when the heading names a sub-area, e.g. HOUSE – DRIVEWAY/LAWN.
    #: This is the main-location → sub-area hierarchy needs, stated by the
    #: script itself rather than inferred later.
    sub_area_raw: str | None
    time_of_day: str | None
    characters_raw: list[str]
    gaps: list[str] = field(default_factory=list)


def _parse_header_body(body: str) -> ParsedHeading:
    """Parse ``INT. MAIN [– SUB-AREA] – TIME [– CHARACTERS]``.

    The time field is the anchor: everything between the location and the time
    is a sub-area, everything after it is cast. Finding the time by lookup
    rather than by position is what makes the optional sub-area segment work.
    """
    gaps: list[str] = []

    # Resolve INT/EXT before splitting — the combined form can contain the
    # separator character itself.
    int_ext: str | None = None
    remainder = body.strip()
    match = _INT_EXT.match(remainder)
    if match:
        raw = re.sub(r"[\s./]", "", match.group(1)).upper()
        int_ext = "INT/EXT" if len(raw) > 3 else raw
        remainder = match.group(2).strip()
    else:
        gaps.append(f"heading does not start with INT./EXT.: {body[:48]!r}")

    segments = [s.strip() for s in remainder.split(EN_DASH) if s.strip()]
    if not segments:
        return ParsedHeading(int_ext, None, None, None, [], gaps + ["heading has no body"])

    location = segments[0]
    rest = segments[1:]

    time_index = next(
        (i for i, s in enumerate(rest) if s.upper() in _KNOWN_TIMES), None
    )
    if time_index is None:
        time_of_day = None
        sub_area = None
        cast_segments = rest
        if rest:
            gaps.append(f"no recognised time of day in heading; segments {rest!r}")
        else:
            gaps.append("heading has no time of day and no character list")
    else:
        time_of_day = rest[time_index].upper()
        sub_area = " – ".join(rest[:time_index]) or None
        cast_segments = rest[time_index + 1 :]

    characters: list[str] = []
    for segment in cast_segments:
        characters += [c.strip() for c in segment.split(",") if c.strip()]

    if location.upper() in {"ANY LOCATION", "UNSPECIFIED"}:
        gaps.append(
            f"heading location is a placeholder ({location!r}) — needs a real set "
            "before anything can be designed"
        )

    return ParsedHeading(int_ext, location or None, sub_area, time_of_day, characters, gaps)


def parse_screenplay(text: str) -> ParsedScreenplay:
    lines = text.splitlines()
    episodes: list[ParsedEpisode] = []
    doc_gaps: list[str] = []

    current_episode: ParsedEpisode | None = None
    current_scene: ParsedScene | None = None
    pending_transition: str | None = None
    pending_time_jump: str | None = None
    intercut_counter = 0

    def close_scene(end_line: int) -> None:
        nonlocal current_scene
        if current_scene is not None:
            current_scene.end_line = end_line
            current_episode.scenes.append(current_scene)
            current_scene = None

    for index, raw_line in enumerate(lines):
        line_no = index + 1
        line = raw_line.strip()
        if not line:
            continue

        episode_match = _EPISODE.match(line)
        if episode_match:
            close_scene(line_no - 1)
            if current_episode is not None:
                current_episode.end_line = line_no - 1
            current_episode = ParsedEpisode(
                number=int(episode_match.group(1)),
                scenes=[],
                start_line=line_no,
                end_line=line_no,
            )
            episodes.append(current_episode)
            pending_transition = None
            continue

        header_match = _SCENE_HEADER.match(line)
        if header_match and current_episode is not None:
            previous = current_scene
            close_scene(line_no - 1)

            suffix = (header_match.group("suffix") or "").replace("/", "").strip()
            number = int(header_match.group("number"))
            label = f"{number}{suffix.upper()}" if suffix else str(number)
            heading = _parse_header_body(header_match.group("body"))

            # Two scenes sharing a number across an INT CUT are one intercut
            # pair playing simultaneously — not a duplicate to be deduped.
            group = None
            if (
                previous is not None
                and pending_transition
                and pending_transition.lower().replace(" ", "") in {"intcut", "intercut"}
            ):
                if previous.intercut_group:
                    group = previous.intercut_group
                else:
                    intercut_counter += 1
                    group = f"ep{current_episode.number}-ic{intercut_counter}"
                    previous.intercut_group = group

            current_scene = ParsedScene(
                episode_number=current_episode.number,
                number=number,
                number_label=label,
                heading_raw=line,
                int_ext=heading.int_ext,
                location_raw=heading.location_raw,
                sub_area_raw=heading.sub_area_raw,
                time_of_day=heading.time_of_day,
                characters_raw=heading.characters_raw,
                action_lines=[],
                dialogue=[],
                transition_out=None,
                time_jump_before=pending_time_jump,
                intercut_group=group,
                start_line=line_no,
                end_line=line_no,
                gaps=heading.gaps,
            )
            if pending_time_jump:
                current_scene.gaps.append(
                    f"time jump immediately before this scene: {pending_time_jump!r} — "
                    "confirm the story-day boundary"
                )
            pending_transition = None
            pending_time_jump = None
            continue

        lowered = line.lower().rstrip(".:")
        if lowered in _TRANSITIONS:
            if current_scene is not None:
                current_scene.transition_out = line.upper()
            pending_transition = line
            continue

        if _TIME_JUMP.match(line):
            # The strongest signal story-day inference gets. Held until the
            # next heading so it lands on the scene it actually moves.
            pending_time_jump = line
            pending_transition = line
            continue

        if current_scene is None:
            if current_episode is not None and line.upper() != line:
                doc_gaps.append(f"line {line_no}: text outside any scene — {line[:60]!r}")
            continue

        dialogue_match = _DIALOGUE.match(line)
        if dialogue_match:
            cue = dialogue_match.group("cue")
            action, emotion = _split_cue(cue)
            current_scene.dialogue.append(
                ParsedDialogue(
                    order=len(current_scene.dialogue),
                    speaker_raw=dialogue_match.group("speaker").strip(),
                    line=dialogue_match.group("line").strip(),
                    delivery_raw=cue.strip() if cue else None,
                    delivery_action=action,
                    delivery_emotion=emotion,
                    line_no=line_no,
                )
            )
        else:
            current_scene.action_lines.append(line)

    close_scene(len(lines))
    if current_episode is not None:
        current_episode.end_line = len(lines)

    screenplay = ParsedScreenplay(episodes=episodes, gaps=doc_gaps)
    _assign_uids(screenplay)
    _check_episode_continuity(screenplay)

    log.info("ingest.parsed", **screenplay.stats)
    return screenplay


def _assign_uids(screenplay: ParsedScreenplay) -> None:
    """Give every scene a key that is unique across the screenplay.

    ``EP4 SC1`` occurs twice — an intercut pair, both halves genuinely numbered
    scene 1. The second gets a ``b`` suffix so downstream stages address them
    separately. Readability matters here: these keys appear in the merge
    questions and reports a human reads.
    """
    seen: dict[str, int] = {}
    for scene in screenplay.scenes:
        base = f"{scene.episode_number}:{scene.number_label}"
        count = seen.get(base, 0)
        seen[base] = count + 1
        scene.uid = base if count == 0 else f"{base}{chr(ord('b') + count - 1)}"


def _check_episode_continuity(screenplay: ParsedScreenplay) -> None:
    """Flag missing episodes rather than letting a gap pass unnoticed.

    A script that jumps 31 → 33 is missing a file, and every count downstream
    is wrong until someone says whether episode 32 exists.
    """
    numbers = [e.number for e in screenplay.episodes]
    if not numbers:
        screenplay.gaps.append("no episodes found — is this a screenplay?")
        return
    expected = set(range(min(numbers), max(numbers) + 1))
    missing = sorted(expected - set(numbers))
    if missing:
        screenplay.gaps.append(
            f"episode(s) {missing} missing from the file — "
            "counts and story-days cannot be final until they are supplied"
        )
    for episode in screenplay.episodes:
        if not episode.scenes:
            screenplay.gaps.append(f"episode {episode.number} has no parseable scenes")

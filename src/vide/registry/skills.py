"""Skill registry.

A skill is a versioned instruction document an agent loads into context. Files
in ``skills/`` are the editable source; the ``skills`` table is what agents read
at runtime. ``sync`` pushes files into the table.

Two properties matter and both come from versioning:

* **Swapping a skill is configuration, not code.** Agents ask by name; the
  registry returns the active version. Activating v2 changes every agent that
  loads it, with no deploy.
* **Every generation records the versions it used** (``skill_versions_json``),
  so a first-pass approval rate can be attributed to a specific skill version
  and two versions can be compared on the same elements.

Content is addressed by hash, so re-syncing an unchanged file is a no-op and an
edited file is caught rather than silently diverging from what the table holds.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog
import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from vide.models.enums import SkillSource
from vide.models.registry import ProjectSkillPin, Skill

log = structlog.get_logger(__name__)

SKILLS_DIR = Path(__file__).resolve().parents[3] / "skills"
_FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n+", re.DOTALL)


class SkillNotFound(LookupError):
    pass


@dataclass(frozen=True, slots=True)
class SkillFile:
    name: str
    version: int
    kind: str
    source: SkillSource
    description: str
    content: str
    path: Path

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.content.encode()).hexdigest()


def parse_skill_file(path: Path) -> SkillFile:
    raw = path.read_text()
    match = _FRONTMATTER.match(raw)
    if not match:
        raise ValueError(f"{path.name} has no YAML frontmatter")
    meta = yaml.safe_load(match.group(1)) or {}
    body = raw[match.end() :]

    missing = {"name", "version", "kind", "source"} - set(meta)
    if missing:
        raise ValueError(f"{path.name} frontmatter is missing {sorted(missing)}")

    return SkillFile(
        name=str(meta["name"]),
        version=int(meta["version"]),
        kind=str(meta["kind"]),
        source=SkillSource(meta["source"]),
        description=str(meta.get("description", "")).strip(),
        content=body,
        path=path,
    )


def discover(directory: Path | None = None) -> list[SkillFile]:
    root = directory or SKILLS_DIR
    return sorted(
        (parse_skill_file(p) for p in root.glob("*.md")),
        key=lambda s: (s.name, s.version),
    )


@dataclass(slots=True)
class SyncResult:
    created: list[str]
    updated: list[str]
    unchanged: list[str]
    activated: list[str]

    @property
    def changed(self) -> bool:
        return bool(self.created or self.updated or self.activated)


def sync(session: Session, directory: Path | None = None) -> SyncResult:
    """Push ``skills/`` into the table.

    A newly-seen name is activated automatically — there is nothing to compare
    it against. An existing name keeps whatever version is already active, so a
    sync never silently changes agent behaviour; promoting a version is a
    separate, deliberate ``activate`` call.
    """
    result = SyncResult([], [], [], [])

    for file in discover(directory):
        existing = session.scalar(
            select(Skill).where(Skill.name == file.name, Skill.version == file.version)
        )

        if existing is None:
            is_first = (
                session.scalar(select(Skill).where(Skill.name == file.name)) is None
            )
            session.add(
                Skill(
                    name=file.name,
                    version=file.version,
                    kind=file.kind,
                    source=file.source,
                    content=file.content,
                    content_hash=file.content_hash,
                    description=file.description,
                    active=is_first,
                )
            )
            result.created.append(f"{file.name} v{file.version}")
            if is_first:
                result.activated.append(f"{file.name} v{file.version}")
        elif existing.content_hash != file.content_hash:
            # Editing a published version in place would break replay: a past
            # generation recorded this version and must still resolve to the
            # bytes it actually ran on.
            log.warning(
                "skill.content_drift",
                skill=file.name,
                version=file.version,
                hint="bump the version in frontmatter instead of editing in place",
            )
            existing.content = file.content
            existing.content_hash = file.content_hash
            existing.description = file.description
            result.updated.append(f"{file.name} v{file.version}")
        else:
            result.unchanged.append(f"{file.name} v{file.version}")

    session.flush()
    return result


def activate(session: Session, name: str, version: int) -> Skill:
    """Make one version the active one for a name. Exactly one wins."""
    target = session.scalar(
        select(Skill).where(Skill.name == name, Skill.version == version)
    )
    if target is None:
        raise SkillNotFound(f"{name} v{version} is not in the registry")

    for skill in session.scalars(select(Skill).where(Skill.name == name)):
        skill.active = skill.id == target.id
    session.flush()
    log.info("skill.activated", skill=name, version=version)
    return target


def resolve(
    session: Session, name: str, *, project_id: Any | None = None
) -> Skill:
    """Return the version an agent should load.

    A project pin wins over the global active version, which is what makes an
    A/B run possible: pin one project to v2 and leave the rest on v1.
    """
    if project_id is not None:
        pin = session.scalar(
            select(ProjectSkillPin).where(
                ProjectSkillPin.project_id == project_id,
                ProjectSkillPin.skill_name == name,
            )
        )
        if pin is not None:
            pinned = session.scalar(
                select(Skill).where(
                    Skill.name == name, Skill.version == pin.skill_version
                )
            )
            if pinned is None:
                raise SkillNotFound(
                    f"project pins {name} to v{pin.skill_version}, which is not loaded"
                )
            return pinned

    active = session.scalar(select(Skill).where(Skill.name == name, Skill.active))
    if active is None:
        raise SkillNotFound(f"no active version of skill {name!r}")
    return active


def load(
    session: Session, names: list[str], *, project_id: Any | None = None
) -> tuple[str, dict[str, int]]:
    """Resolve several skills and return their text plus the versions used.

    The second value goes straight onto ``generations.skill_versions_json`` —
    the record of what produced an output.
    """
    parts: list[str] = []
    versions: dict[str, int] = {}
    for name in names:
        skill = resolve(session, name, project_id=project_id)
        parts.append(skill.content.strip())
        versions[skill.name] = skill.version
    return "\n\n---\n\n".join(parts), versions

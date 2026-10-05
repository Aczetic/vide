"""`python -m vide.registry <command>` — manage the skill registry.

    list                    what is loaded, and which version is active
    sync                    push skills/ into the table
    activate <name> <ver>   promote a version
    show <name>             print the resolved content an agent would load
"""

from __future__ import annotations

import argparse
import sys

from sqlalchemy import select

from vide.db import session_scope
from vide.models.registry import Skill
from vide.registry.skills import SkillNotFound, activate, discover, resolve, sync


def cmd_list() -> int:
    with session_scope() as session:
        skills = list(session.scalars(select(Skill).order_by(Skill.name, Skill.version)))
        if not skills:
            print("registry is empty — run: python -m vide.registry sync")
            return 0
        width = max(len(s.name) for s in skills)
        print()
        for skill in skills:
            mark = "*" if skill.active else " "
            size = f"{len(skill.content):,}c"
            print(
                f" {mark} {skill.name:<{width}}  v{skill.version}  "
                f"{skill.kind:<12} {skill.source:<18} {size:>9}  {skill.content_hash[:8]}"
            )
        print("\n * = active version agents resolve to\n")
    return 0


def cmd_sync() -> int:
    files = discover()
    if not files:
        print("no skill files found in skills/")
        return 1
    with session_scope() as session:
        result = sync(session)
    for label, items in (
        ("created", result.created),
        ("updated", result.updated),
        ("activated", result.activated),
    ):
        for item in items:
            print(f"  {label:<10} {item}")
    if result.unchanged:
        print(f"  unchanged  {len(result.unchanged)} skill(s)")
    if not result.changed:
        print("  registry already in sync")
    return 0


def cmd_activate(name: str, version: int) -> int:
    try:
        with session_scope() as session:
            activate(session, name, version)
    except SkillNotFound as exc:
        print(f"  {exc}")
        return 1
    print(f"  {name} v{version} is now active")
    return 0


def cmd_show(name: str) -> int:
    try:
        with session_scope() as session:
            skill = resolve(session, name)
            print(f"# {skill.name} v{skill.version} ({skill.source})\n")
            print(skill.content)
    except SkillNotFound as exc:
        print(f"  {exc}")
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m vide.registry")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="show loaded skills and active versions")
    sub.add_parser("sync", help="push skills/ into the table")
    act = sub.add_parser("activate", help="promote a version")
    act.add_argument("name")
    act.add_argument("version", type=int)
    show = sub.add_parser("show", help="print what an agent would load")
    show.add_argument("name")

    args = parser.parse_args(argv)
    match args.command:
        case "list":
            return cmd_list()
        case "sync":
            return cmd_sync()
        case "activate":
            return cmd_activate(args.name, args.version)
        case "show":
            return cmd_show(args.name)
    return 1


if __name__ == "__main__":
    sys.exit(main())

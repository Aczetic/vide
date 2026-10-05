"""`python -m vide.jobs <command>` — run and inspect workers.

    run [--concurrency N] [--types a,b]   drain the queue until interrupted
    stats                                  counts by status
    reset                                  return expired leases to the pool
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

import structlog

from vide.config import get_settings
from vide.db import session_scope
from vide.jobs import queue
from vide.jobs.worker import WorkerConfig, registered_types, run_worker


def _configure_logging() -> None:
    logging.basicConfig(
        format="%(message)s", level=getattr(logging, get_settings().log_level, "INFO")
    )
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="%H:%M:%S"),
            structlog.dev.ConsoleRenderer(),
        ]
    )


def cmd_run(concurrency: int, types: str | None) -> int:
    _configure_logging()
    selected = [t.strip() for t in types.split(",")] if types else []
    known = registered_types()
    if not known:
        print("no job handlers are registered yet — nothing for a worker to do.")
        print("Handlers arrive with the a later stage pipeline.")
        return 1
    unknown = set(selected) - set(known)
    if unknown:
        print(f"unknown job types: {sorted(unknown)}")
        print(f"registered: {known}")
        return 1
    asyncio.run(run_worker(WorkerConfig(concurrency=concurrency, types=selected)))
    return 0


def cmd_stats() -> int:
    with session_scope() as session:
        counts = queue.stats(session)
    if not counts:
        print("queue is empty")
        return 0
    width = max(len(k) for k in counts)
    for status, count in sorted(counts.items()):
        print(f"  {status:<{width}}  {count:>6,}")
    return 0


def cmd_reset() -> int:
    with session_scope() as session:
        freed = queue.reset_stuck(session)
    print(f"  returned {freed} expired lease(s) to the pool")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m vide.jobs")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="drain the queue until interrupted")
    run.add_argument("--concurrency", type=int, default=8)
    run.add_argument("--types", help="comma-separated job types; default all")
    sub.add_parser("stats", help="counts by status")
    sub.add_parser("reset", help="return expired leases to the pool")

    args = parser.parse_args(argv)
    match args.command:
        case "run":
            return cmd_run(args.concurrency, args.types)
        case "stats":
            return cmd_stats()
        case "reset":
            return cmd_reset()
    return 1


if __name__ == "__main__":
    sys.exit(main())

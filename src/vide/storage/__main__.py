"""`python -m vide.storage <command>` — inspect and reconcile R2.

    status     credential check + declared layout, no network call
    reconcile  compare storage.yaml against the live bucket
    reconcile --apply   create markers for declared prefixes that are missing
"""

from __future__ import annotations

import argparse
import sys

from vide.storage.layout import get_layout
from vide.storage.r2 import check_credentials, reconcile


def _human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024.0
    return f"{n}B"


def cmd_status() -> int:
    layout = get_layout()
    creds = check_credentials()

    print(f"bucket:   {layout.bucket}")
    print(f"region:   {layout.region}")
    print(f"creds:    {'ok' if creds.ok else 'PROBLEM'}")
    if not creds.ok:
        print(f"          {creds.problem}")
        if creds.hint:
            print(f"          {creds.hint}")

    print(f"\ndeclared prefixes ({len(layout.prefixes)}):")
    width = max(len(n) for n in layout.prefixes)
    for name, prefix in layout.prefixes.items():
        flags = []
        if prefix.public:
            flags.append("public")
        if prefix.retention != "keep":
            flags.append(prefix.retention)
        suffix = f"  [{', '.join(flags)}]" if flags else ""
        print(f"  {name:<{width}}  {prefix.template}{suffix}")

    if layout.lifecycle:
        print("\nlifecycle intent:")
        for rule in layout.lifecycle:
            window = f"expire after {rule.expire_days}d" if rule.expire_days else "keep"
            print(f"  {rule.prefix:<16} {window}")
    return 0 if creds.ok else 1


def cmd_reconcile(apply: bool, allow_delete: bool) -> int:
    report = reconcile(apply=apply, allow_delete=allow_delete)

    if not report.reachable:
        print(f"bucket {report.bucket}: UNREACHABLE")
        print(f"  {report.error}")
        return 1

    print(f"bucket {report.bucket}: reachable")
    print(f"  objects: {report.object_count:,}  ({_human(report.total_bytes)})")

    if report.undeclared:
        print("\n  prefixes in the bucket that storage.yaml does not declare:")
        for prefix in report.undeclared:
            print(f"    {prefix}")
        if allow_delete:
            print("\n  These hold objects, so they were kept. Empty ones were removed.")
        else:
            print("\n  Add them to storage.yaml, or pass --allow-delete to remove the")
            print("  empty ones. Prefixes with content are never deleted.")

    if report.empty_declared:
        print("\n  declared but not present yet:")
        for prefix in report.empty_declared:
            print(f"    {prefix}")
        if not apply:
            print("\n  Run with --apply to create them.")

    if report.in_sync and not report.empty_declared:
        print("\n  in sync with storage.yaml")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m vide.storage")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status", help="credential check and declared layout")
    rec = sub.add_parser("reconcile", help="compare storage.yaml against the bucket")
    rec.add_argument(
        "--apply",
        action="store_true",
        help="create markers for declared prefixes that are missing",
    )
    rec.add_argument(
        "--allow-delete",
        action="store_true",
        help="also remove undeclared prefixes that are empty; "
        "prefixes holding objects are always kept",
    )

    args = parser.parse_args(argv)
    if args.command == "status":
        return cmd_status()
    return cmd_reconcile(args.apply, args.allow_delete)


if __name__ == "__main__":
    sys.exit(main())

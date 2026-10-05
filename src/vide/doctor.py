"""`python -m vide.doctor` — check every external dependency and report.

Each check is cheap and side-effect free: no generation is submitted, nothing is
written. Run it after changing .env, before a deploy, or whenever something
behaves oddly and it isn't obvious which layer is at fault.
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass

import httpx
from sqlalchemy import text

from vide.config import get_settings
from vide.providers.higgsfield import BASE_URL as HF_BASE

OK, WARN, FAIL, SKIP = "ok", "warn", "fail", "skip"

_MARK = {OK: "PASS", WARN: "WARN", FAIL: "FAIL", SKIP: "SKIP"}


@dataclass(slots=True)
class Check:
    name: str
    state: str
    detail: str = ""
    hint: str = ""


async def check_database() -> Check:
    try:
        from vide.db import engine

        with engine.connect() as conn:
            version = conn.execute(text("SHOW server_version")).scalar()
            tables = conn.execute(
                text(
                    "SELECT count(*) FROM information_schema.tables "
                    "WHERE table_schema = 'public'"
                )
            ).scalar()
            has_vector = conn.execute(
                text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
            ).scalar()
        if not has_vector:
            return Check(
                "database", WARN, f"Postgres {version}, {tables} tables, pgvector MISSING",
                "Run: docker exec vide-db psql -U vide -d vide "
                "-c 'CREATE EXTENSION vector'",
            )
        return Check("database", OK, f"Postgres {version}, {tables} tables, pgvector ready")
    except Exception as exc:  # noqa: BLE001 — the point is to report, not raise
        return Check(
            "database", FAIL, str(exc)[:160],
            "Is the container up? docker compose up -d",
        )


async def check_openrouter() -> Check:
    s = get_settings()
    if not s.openrouter_api_key:
        return Check("openrouter", FAIL, "OPENROUTER_API_KEY is not set")
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(
                f"{s.openrouter_base_url.rstrip('/')}/models",
                headers={"Authorization": f"Bearer {s.openrouter_api_key}"},
            )
        if r.status_code != 200:
            return Check("openrouter", FAIL, f"HTTP {r.status_code}: {r.text[:120]}")
        models = r.json().get("data", [])
        claude = sum(1 for m in models if m["id"].startswith("anthropic/"))
        return Check("openrouter", OK, f"{len(models)} models reachable ({claude} Claude)")
    except Exception as exc:  # noqa: BLE001
        return Check("openrouter", FAIL, str(exc)[:160])


async def check_anthropic() -> Check:
    s = get_settings()
    if not s.anthropic_api_key:
        return Check(
            "anthropic", SKIP, "ANTHROPIC_API_KEY not set",
            "Optional. Claude tasks route via OpenRouter instead, without "
            "prompt caching or batch.",
        )
    try:
        import anthropic

        client = anthropic.AsyncAnthropic(api_key=s.anthropic_api_key)
        models = await client.models.list()
        return Check("anthropic", OK, f"{len(models.data)} models reachable")
    except Exception as exc:  # noqa: BLE001
        return Check("anthropic", FAIL, str(exc)[:160])


async def check_higgsfield() -> Check:
    s = get_settings()
    if not s.higgsfield_api_key:
        return Check(
            "higgsfield", SKIP, "HIGGSFIELD_API_KEY not set",
            "Needed from a later stage, when image generation starts.",
        )
    try:
        # The signed-upload endpoint validates auth without queueing a generation.
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                f"{HF_BASE}/files/generate-upload-url",
                headers={
                    "Authorization": f"Key {s.higgsfield_api_key.strip()}",
                    "Content-Type": "application/json",
                },
                json={"content_type": "image/png"},
            )
        if r.status_code != 200:
            return Check("higgsfield", FAIL, f"HTTP {r.status_code}: {r.text[:120]}")
        return Check("higgsfield", OK, "auth accepted, upload slot issued")
    except Exception as exc:  # noqa: BLE001
        return Check("higgsfield", FAIL, str(exc)[:160])


async def check_storage() -> Check:
    from vide.storage import check_credentials, reconcile

    creds = check_credentials()
    if not creds.ok:
        return Check("storage (R2)", FAIL, creds.problem or "", creds.hint or "")
    report = await asyncio.to_thread(reconcile)
    if not report.reachable:
        return Check("storage (R2)", FAIL, report.error or "unreachable")
    detail = f"bucket {report.bucket}: {report.object_count:,} objects"
    if report.undeclared:
        return Check(
            "storage (R2)", WARN,
            f"{detail}; {len(report.undeclared)} undeclared prefix(es)",
            "python -m vide.storage reconcile",
        )
    return Check("storage (R2)", OK, detail)


async def run() -> int:
    checks = await asyncio.gather(
        check_database(),
        check_openrouter(),
        check_anthropic(),
        check_higgsfield(),
        check_storage(),
    )

    width = max(len(c.name) for c in checks)
    print()
    for check in checks:
        print(f"  {_MARK[check.state]}  {check.name:<{width}}  {check.detail}")
        if check.hint:
            print(f"        {' ' * width}  → {check.hint}")

    failed = [c for c in checks if c.state == FAIL]
    warned = [c for c in checks if c.state == WARN]
    print()
    if failed:
        print(f"  {len(failed)} check(s) failing: {', '.join(c.name for c in failed)}")
    elif warned:
        print(f"  {len(warned)} warning(s), nothing blocking")
    else:
        print("  all checks passing")
    print()
    return 1 if failed else 0


def main() -> int:
    return asyncio.run(run())


if __name__ == "__main__":
    sys.exit(main())

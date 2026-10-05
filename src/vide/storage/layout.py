"""Parse storage.yaml and build object keys from it.

Call sites ask for a key by name and pass the parts; they never concatenate
strings. That keeps the layout editable in one file and makes a missing part a
loud error instead of a key with a literal ``{project_id}`` in it.
"""

from __future__ import annotations

import string
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

DEFAULT_MANIFEST = Path(__file__).resolve().parents[3] / "storage.yaml"


@dataclass(frozen=True, slots=True)
class Prefix:
    name: str
    template: str
    purpose: str
    retention: str
    public: bool

    @property
    def fields(self) -> frozenset[str]:
        return frozenset(
            f for _, f, _, _ in string.Formatter().parse(self.template) if f
        )

    @property
    def root(self) -> str:
        """The fixed leading segments, for listing and reconciliation.

        ``projects/{project_id}/sources/...`` has root ``projects/`` — everything
        before the first placeholder.
        """
        head = self.template.split("{", 1)[0]
        return head if head.endswith("/") else head.rsplit("/", 1)[0] + "/"

    def key(self, **parts: Any) -> str:
        missing = self.fields - set(parts)
        if missing:
            raise KeyError(
                f"prefix {self.name!r} needs {sorted(missing)} to build a key"
            )
        return self.template.format(**parts)


@dataclass(frozen=True, slots=True)
class LifecycleRule:
    prefix: str
    expire_days: int | None
    reason: str


@dataclass(frozen=True, slots=True)
class StorageLayout:
    bucket: str
    region: str
    prefixes: dict[str, Prefix]
    lifecycle: tuple[LifecycleRule, ...]
    ingest_policy: dict[str, Any]

    def prefix(self, name: str) -> Prefix:
        try:
            return self.prefixes[name]
        except KeyError as exc:
            known = ", ".join(sorted(self.prefixes))
            raise KeyError(f"unknown prefix {name!r}; declared: {known}") from exc

    def key(self, prefix_name: str, **parts: Any) -> str:
        return self.prefix(prefix_name).key(**parts)

    @property
    def roots(self) -> tuple[str, ...]:
        """Distinct fixed roots, for reconciliation against the live bucket."""
        return tuple(sorted({p.root for p in self.prefixes.values()}))


def load_layout(path: Path | str | None = None) -> StorageLayout:
    manifest = Path(path) if path else DEFAULT_MANIFEST
    raw = yaml.safe_load(manifest.read_text())

    prefixes = {
        name: Prefix(
            name=name,
            template=spec["template"],
            purpose=(spec.get("purpose") or "").strip(),
            retention=spec.get("retention", "keep"),
            public=bool(spec.get("public", False)),
        )
        for name, spec in (raw.get("prefixes") or {}).items()
    }
    lifecycle = tuple(
        LifecycleRule(
            prefix=rule["prefix"],
            expire_days=rule.get("expire_days"),
            reason=(rule.get("reason") or "").strip(),
        )
        for rule in (raw.get("lifecycle") or [])
    )
    return StorageLayout(
        bucket=raw["bucket"],
        region=raw.get("region", "auto"),
        prefixes=prefixes,
        lifecycle=lifecycle,
        ingest_policy=raw.get("ingest_policy") or {},
    )


@lru_cache
def get_layout() -> StorageLayout:
    return load_layout()

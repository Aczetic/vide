"""R2 client and the reconcile check.

R2 is S3-compatible, so this is boto3 with an endpoint override and
``region_name="auto"``. Two things differ from S3 in ways that bite:

* R2 access key IDs are 32 hex characters; secrets are 64. Pasting the secret
  into both fields fails at ``ListBuckets`` with a length complaint rather than
  an auth error, which reads as a client bug. ``check_credentials`` names it.
* Object storage has no real directories. A "folder" is a key prefix, and an
  empty one does not exist. Reconciliation therefore reports what prefixes are
  *in use*, and creating one writes a zero-byte marker so the console shows it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import boto3
import structlog
from botocore.config import Config
from botocore.exceptions import ClientError

from vide.config import get_settings
from vide.storage.layout import StorageLayout, get_layout

log = structlog.get_logger(__name__)

#: Written into an otherwise-empty prefix so it is visible in the R2 console.
KEEP_MARKER = ".keep"


@dataclass(slots=True)
class CredentialCheck:
    ok: bool
    problem: str | None = None
    hint: str | None = None


@dataclass(slots=True)
class ReconcileReport:
    bucket: str
    reachable: bool
    declared_roots: tuple[str, ...] = ()
    #: Prefixes present in the bucket that the manifest does not declare.
    undeclared: list[str] = field(default_factory=list)
    #: Declared roots with no objects under them yet.
    empty_declared: list[str] = field(default_factory=list)
    object_count: int = 0
    total_bytes: int = 0
    error: str | None = None

    @property
    def in_sync(self) -> bool:
        return self.reachable and not self.undeclared


def check_credentials() -> CredentialCheck:
    """Validate the credential *shape* before making a call.

    Catches the common paste errors with a specific message, because the
    server-side errors for these are misleading.
    """
    s = get_settings()
    if not s.storage_endpoint:
        return CredentialCheck(False, "STORAGE_ENDPOINT is not set")
    if not s.storage_access_key or not s.storage_secret_key:
        return CredentialCheck(False, "STORAGE_ACCESS_KEY or STORAGE_SECRET_KEY is empty")

    if s.storage_access_key == s.storage_secret_key:
        return CredentialCheck(
            False,
            "STORAGE_ACCESS_KEY and STORAGE_SECRET_KEY hold the same value",
            "R2 issues two distinct values. The Access Key ID is 32 hex "
            "characters; the Secret Access Key is 64. Copy the Access Key ID "
            "into STORAGE_ACCESS_KEY.",
        )
    if not re.fullmatch(r"[0-9a-f]{32}", s.storage_access_key):
        return CredentialCheck(
            False,
            f"STORAGE_ACCESS_KEY is {len(s.storage_access_key)} characters",
            "An R2 Access Key ID is exactly 32 hex characters. A 64-character "
            "value is the Secret Access Key.",
        )
    return CredentialCheck(True)


def get_client() -> Any:
    s = get_settings()
    return boto3.client(
        "s3",
        endpoint_url=s.storage_endpoint,
        aws_access_key_id=s.storage_access_key,
        aws_secret_access_key=s.storage_secret_key,
        region_name=get_layout().region,
        config=Config(signature_version="s3v4", retries={"max_attempts": 3}),
    )


def reconcile(
    layout: StorageLayout | None = None,
    *,
    apply: bool = False,
    allow_delete: bool = False,
) -> ReconcileReport:
    """Compare the declared layout against the live bucket.

    ``apply`` creates markers for declared prefixes that do not exist yet.
    ``allow_delete`` additionally removes undeclared prefixes — but only when
    they are empty. A prefix holding real objects is always reported and never
    touched, whatever the flags say.
    """
    layout = layout or get_layout()
    report = ReconcileReport(
        bucket=layout.bucket, reachable=False, declared_roots=layout.roots
    )

    creds = check_credentials()
    if not creds.ok:
        report.error = creds.problem + (f" — {creds.hint}" if creds.hint else "")
        return report

    client = get_client()
    try:
        seen_roots: set[str] = set()
        paginator = client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=layout.bucket):
            for obj in page.get("Contents", []):
                report.object_count += 1
                report.total_bytes += obj["Size"]
                seen_roots.add(_root_of(obj["Key"]))

        # Also pick up prefixes that exist only as console-created folders.
        top = client.list_objects_v2(Bucket=layout.bucket, Delimiter="/")
        for cp in top.get("CommonPrefixes", []):
            seen_roots.add(cp["Prefix"])

        report.reachable = True
    except ClientError as exc:
        err = exc.response["Error"]
        report.error = f"{err.get('Code')}: {err.get('Message')}"
        return report

    declared = set(layout.roots)
    report.undeclared = sorted(seen_roots - declared)
    report.empty_declared = sorted(declared - seen_roots)

    if apply and report.empty_declared:
        for root in report.empty_declared:
            key = f"{root}{KEEP_MARKER}"
            client.put_object(
                Bucket=layout.bucket,
                Key=key,
                Body=b"",
                ContentType="text/plain",
            )
            log.info("storage.prefix_created", key=key)
        report.empty_declared = []

    if allow_delete and report.undeclared:
        report.undeclared = _delete_empty_prefixes(
            client, layout.bucket, report.undeclared
        )

    return report


def _delete_empty_prefixes(client: Any, bucket: str, prefixes: list[str]) -> list[str]:
    """Remove undeclared prefixes that hold nothing. Returns what survived.

    "Empty" means no keys beyond the zero-byte folder marker itself. Anything
    with real content is left alone and reported, because losing a generated
    asset to a layout tidy-up is not a recoverable mistake.
    """
    survivors: list[str] = []
    for prefix in prefixes:
        listing = client.list_objects_v2(Bucket=bucket, Prefix=prefix)
        contents = listing.get("Contents", [])
        substantive = [o for o in contents if o["Size"] > 0 or not o["Key"].endswith("/")]
        if substantive:
            log.warning(
                "storage.prefix_kept",
                prefix=prefix,
                objects=len(substantive),
                reason="not empty",
            )
            survivors.append(prefix)
            continue
        for obj in contents:
            client.delete_object(Bucket=bucket, Key=obj["Key"])
        log.info("storage.prefix_deleted", prefix=prefix)
    return survivors


def _root_of(key: str) -> str:
    """First path segment of a key, as a prefix with a trailing slash."""
    head, sep, _ = key.partition("/")
    return f"{head}/" if sep else key

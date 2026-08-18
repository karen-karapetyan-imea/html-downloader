"""Crawl job manifest written next to HTML and results.jsonl."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass
class Manifest:
    marketplace: str
    crawl_date: str
    started_at: str
    finished_at: str | None
    workers: int
    rps: float
    proxy_file: str
    url_count: int
    status: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def new_manifest(
    *,
    marketplace: str,
    crawl_date: str,
    workers: int,
    rps: float,
    proxy_file: str,
    url_count: int,
) -> Manifest:
    return Manifest(
        marketplace=marketplace,
        crawl_date=crawl_date,
        started_at=_utc_now(),
        finished_at=None,
        workers=workers,
        rps=rps,
        proxy_file=proxy_file,
        url_count=url_count,
        status="running",
    )


def write_manifest(path: Path, manifest: Manifest) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest.to_dict(), indent=2) + "\n", encoding="utf-8")


def finish_manifest(path: Path, manifest: Manifest, *, status: str) -> Manifest:
    updated = Manifest(
        marketplace=manifest.marketplace,
        crawl_date=manifest.crawl_date,
        started_at=manifest.started_at,
        finished_at=_utc_now(),
        workers=manifest.workers,
        rps=manifest.rps,
        proxy_file=manifest.proxy_file,
        url_count=manifest.url_count,
        status=status,
    )
    write_manifest(path, updated)
    return updated

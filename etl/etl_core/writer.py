"""Per-chunk processing (runs inside worker processes) and crawl finalization helpers.

A chunk is done when its index is recorded on the etl_crawls ledger. Stage rows use a
deterministic _id, so a retried chunk replaces the same scratch documents.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import date
from typing import Any

from etl_core.crawl import CrawlFile, file_mtime, parse_timestamp
from etl_core.dataset import DatasetSpec
from etl_core.sources import parse_html
from etl_core.store import ChunkStats, EntityStore, MongoConfig

MAX_ERROR_LENGTH = 500


def process_file[RowT](
    dataset: DatasetSpec[RowT], platform: str, file: CrawlFile, stats: ChunkStats
) -> list[RowT]:
    mapper = dataset.mapper(platform)
    try:
        with open(file.path, encoding="utf-8", errors="replace") as fh:
            parsed = parse_html(platform, fh.read(), file.url)
        if not mapper.is_relevant(parsed):
            stats.skipped += 1
            return []
        crawled_at = parse_timestamp(parsed.get("crawled_at")) or file.crawled_at or file_mtime(file.path)
        rows = mapper.to_rows(parsed, file.filename, crawled_at)
    except Exception as exc:  # one bad page must not stop the run
        stats.errors.append(  # type: ignore[union-attr]
            {"filename": file.filename, "error": f"{type(exc).__name__}: {exc}"[:MAX_ERROR_LENGTH]}
        )
        return []
    if not rows:
        stats.no_id += 1
    return rows


def process_chunk(
    dataset: DatasetSpec[Any],
    platform: str,
    index: int,
    files: list[CrawlFile],
    crawl_id: str,
    crawl_date: date,
    mongo_uri: str,
    mongo_db: str,
) -> ChunkStats:
    stats = ChunkStats(index=index, files=len(files))
    rows = [row for f in files for row in process_file(dataset, platform, f, stats)]
    stats.rows = len(rows)
    store = EntityStore(MongoConfig(uri=mongo_uri, database=mongo_db))
    try:
        store.write_chunk(
            crawl_id,
            dataset.name,
            platform,
            crawl_date,
            index,
            rows,
            dataset.key_column,
            dataset.within_crawl_order,
            stats,
        )
    finally:
        store.close()
    return stats


def aggregate_chunk_stats(stats_list: list[ChunkStats]) -> dict[str, Any]:
    totals = {"chunks": 0, "files_total": 0, "rows": 0, "skipped": 0, "no_id": 0, "errors": 0}
    for stats in stats_list:
        totals["chunks"] += 1
        totals["files_total"] += stats.files
        totals["rows"] += stats.rows
        totals["skipped"] += stats.skipped
        totals["no_id"] += stats.no_id
        totals["errors"] += len(stats.errors or [])
    return totals


def chunk_stats_as_dict(stats: ChunkStats) -> dict[str, Any]:
    return asdict(stats)

"""Per-chunk processing (runs inside worker processes) and snapshot finalization.

A chunk is done when its stats file `_chunks/part-NNNNN.json` exists. The Parquet part is
renamed into place first, so a chunk interrupted between the two renames is simply redone.
"""

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import pyarrow as pa

from artists.crawl import CrawlFile, file_mtime, parse_timestamp
from artists.layout import (
    CHUNKS_DIR,
    ERRORS_NAME,
    MANIFEST_NAME,
    atomic_write_table,
    atomic_write_text,
    chunk_stats_path,
    part_name,
)
from artists.mappers import get_mapper
from artists.schema import ArtistRow, to_table
from artists.sources import parse_html

ERRORS_SCHEMA = pa.schema([pa.field("filename", pa.string()), pa.field("error", pa.string())])
MAX_ERROR_LENGTH = 500


@dataclass(slots=True)
class ChunkStats:
    index: int
    files: int = 0
    artist_rows: int = 0
    skipped: int = 0  # not an artist page
    no_id: int = 0  # artist page without an artist id
    errors: list[dict[str, str]] = field(default_factory=list)


def process_file(platform: str, file: CrawlFile, stats: ChunkStats) -> ArtistRow | None:
    mapper = get_mapper(platform)
    try:
        with open(file.path, encoding="utf-8", errors="replace") as fh:
            parsed = parse_html(platform, fh.read(), file.url)
        if not mapper.is_artist_page(parsed):
            stats.skipped += 1
            return None
        crawled_at = parse_timestamp(parsed.get("crawled_at")) or file.crawled_at or file_mtime(file.path)
        row = mapper.to_artist(parsed, file.filename, crawled_at)
    except Exception as exc:  # one bad page must not stop the run; it is recorded in _errors.parquet
        stats.errors.append(
            {"filename": file.filename, "error": f"{type(exc).__name__}: {exc}"[:MAX_ERROR_LENGTH]}
        )
        return None
    if row is None:
        stats.no_id += 1
    return row


def process_chunk(platform: str, index: int, files: list[CrawlFile], snapshot: str) -> ChunkStats:
    stats = ChunkStats(index=index, files=len(files))
    rows = [row for f in files if (row := process_file(platform, f, stats)) is not None]
    stats.artist_rows = len(rows)
    snapshot_path = Path(snapshot)
    if rows:
        atomic_write_table(snapshot_path / part_name(index), to_table(rows))
    atomic_write_text(chunk_stats_path(snapshot_path, index), json.dumps(asdict(stats)))
    return stats


def completed_chunks(snapshot: Path) -> set[int]:
    chunks = snapshot / CHUNKS_DIR
    if not chunks.is_dir():
        return set()
    return {int(p.stem.removeprefix("part-")) for p in chunks.glob("part-*.json")}


def finalize_snapshot(snapshot: Path, run_info: dict[str, Any]) -> dict[str, Any]:
    """Aggregate chunk stats into _manifest.json and write all page errors to _errors.parquet."""
    totals = {"chunks": 0, "files_total": 0, "artist_rows": 0, "skipped": 0, "no_id": 0, "errors": 0}
    errors: list[dict[str, str]] = []
    for path in sorted((snapshot / CHUNKS_DIR).glob("part-*.json")):
        stats = json.loads(path.read_text(encoding="utf-8"))
        totals["chunks"] += 1
        totals["files_total"] += stats["files"]
        totals["artist_rows"] += stats["artist_rows"]
        totals["skipped"] += stats["skipped"]
        totals["no_id"] += stats["no_id"]
        errors += stats["errors"]
    totals["errors"] = len(errors)

    errors_path = snapshot / ERRORS_NAME
    if errors:
        atomic_write_table(errors_path, pa.Table.from_pylist(errors, schema=ERRORS_SCHEMA))
    else:
        errors_path.unlink(missing_ok=True)

    manifest = {**run_info, **totals}
    atomic_write_text(snapshot / MANIFEST_NAME, json.dumps(manifest, indent=2, default=str))
    return manifest

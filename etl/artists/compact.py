"""`compact`: dedup each finished snapshot of a platform and rebuild current/ with DuckDB.

Rule: the latest crawl wins per platform_artist_id, row by row (no column-level merging),
so a value the artist removed does not survive from an older crawl.
"""

import json
import logging
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import pyarrow.parquet as pq

from artists.layout import (
    MANIFEST_NAME,
    PARQUET_COMPRESSION,
    atomic_write_text,
    current_dir,
    part_name,
    platform_snapshots_dir,
    scratch_dir,
    swap_dir,
)
from artists.schema import COLUMNS, CRAWL_DATE_COLUMN
from artists.sources import get_source

log = logging.getLogger(__name__)

SNAPSHOT_ORDER = "crawled_at DESC NULLS LAST, source_file DESC"
CURRENT_ORDER = f"crawled_at DESC NULLS LAST, {CRAWL_DATE_COLUMN} DESC, source_file DESC"


@dataclass(frozen=True, slots=True)
class CompactResult:
    platform: str
    snapshots: int
    snapshots_compacted: int
    current_rows: int


def compact(platform: str, out: Path) -> CompactResult:
    get_source(platform)
    snapshots = _finished_snapshots(platform_snapshots_dir(out, platform))
    if not snapshots:
        raise FileNotFoundError(f"no finished snapshots for {platform} under {out}")
    scratch = scratch_dir(out)
    scratch.mkdir(parents=True, exist_ok=True)
    with duckdb.connect() as con:
        compacted = sum(_dedup_snapshot(con, s, scratch / f"{platform}__{s.name}") for s in snapshots)
        rows = _rebuild_current(
            con, platform, snapshots, current_dir(out, platform), scratch / f"{platform}__current"
        )
    log.info(
        "%s: %d snapshots (%d compacted now), current has %d artists",
        platform,
        len(snapshots),
        compacted,
        rows,
    )
    return CompactResult(platform, len(snapshots), compacted, rows)


def _finished_snapshots(platform_dir: Path) -> list[Path]:
    """Snapshots with a manifest; a run still in progress (or killed) has none and is left alone."""
    if not platform_dir.is_dir():
        return []
    snapshots = []
    for d in sorted(platform_dir.glob(f"{CRAWL_DATE_COLUMN}=*")):
        if not d.is_dir():
            continue
        if (d / MANIFEST_NAME).is_file():
            snapshots.append(d)
        else:
            log.warning("skipping unfinished snapshot %s (no %s)", d, MANIFEST_NAME)
    return snapshots


def _dedup_snapshot(con: duckdb.DuckDBPyConnection, snapshot: Path, work: Path) -> bool:
    manifest = _read_json(snapshot / MANIFEST_NAME)
    parts = sorted(snapshot.glob("part-*.parquet"))
    if manifest.get("compacted_at") and len(parts) <= 1:
        return False
    shutil.rmtree(work, ignore_errors=True)
    shutil.copytree(snapshot, work, ignore=shutil.ignore_patterns("part-*.parquet"))
    rows = _write_latest(con, parts, work / part_name(0), SNAPSHOT_ORDER, hive=False) if parts else 0
    manifest.update(compacted_at=datetime.now(UTC).isoformat(), rows_after_dedup=rows)
    atomic_write_text(work / MANIFEST_NAME, json.dumps(manifest, indent=2, default=str))
    swap_dir(work, snapshot)
    return True


def _rebuild_current(
    con: duckdb.DuckDBPyConnection, platform: str, snapshots: list[Path], target: Path, work: Path
) -> int:
    parts = [p for s in snapshots for p in sorted(s.glob("part-*.parquet"))]
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    rows = _write_latest(con, parts, work / part_name(0), CURRENT_ORDER, hive=True) if parts else 0
    atomic_write_text(
        work / MANIFEST_NAME,
        json.dumps(
            {
                "platform": platform,
                "built_at": datetime.now(UTC).isoformat(),
                "snapshots": [s.name.split("=", 1)[1] for s in snapshots],
                "rows": rows,
            },
            indent=2,
        ),
    )
    swap_dir(work, target)
    return rows


def _write_latest(
    con: duckdb.DuckDBPyConnection, parts: list[Path], target: Path, order: str, *, hive: bool
) -> int:
    files = "[" + ", ".join(_sql_literal(str(p)) for p in parts) + "]"
    columns = ", ".join(f'"{c}"' for c in COLUMNS)
    con.execute(f"""
        COPY (
            SELECT {columns}
            FROM read_parquet({files}, hive_partitioning = {str(hive).lower()})
            QUALIFY row_number() OVER (PARTITION BY platform_artist_id ORDER BY {order}) = 1
            ORDER BY platform_artist_id
        ) TO {_sql_literal(str(target))} (FORMAT parquet, COMPRESSION {PARQUET_COMPRESSION})
    """)
    return pq.ParquetFile(target).metadata.num_rows


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))

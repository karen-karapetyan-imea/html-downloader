"""Output dataset layout and atomic file helpers.

<out>/snapshots/platform_name=<p>/crawl_date=<YYYY-MM-DD>/part-*.parquet, _manifest.json, _errors.parquet
<out>/current/platform_name=<p>/part-00000.parquet
<out>/.tmp/                       scratch space for compaction (outside the partition tree)
"""

import os
import shutil
from datetime import date
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from etl_core.schema import CRAWL_DATE_COLUMN, PLATFORM_COLUMN

MANIFEST_NAME = "_manifest.json"
ERRORS_NAME = "_errors.parquet"
RUN_NAME = "_run.json"
CHUNKS_DIR = "_chunks"
TMP_SUFFIX = ".tmp"
PARQUET_COMPRESSION = "zstd"


def platform_snapshots_dir(out: Path, platform: str) -> Path:
    return out / "snapshots" / f"{PLATFORM_COLUMN}={platform}"


def snapshot_dir(out: Path, platform: str, crawl_date: date) -> Path:
    return platform_snapshots_dir(out, platform) / f"{CRAWL_DATE_COLUMN}={crawl_date.isoformat()}"


def current_dir(out: Path, platform: str) -> Path:
    return out / "current" / f"{PLATFORM_COLUMN}={platform}"


def scratch_dir(out: Path) -> Path:
    return out / ".tmp"


def part_name(index: int) -> str:
    return f"part-{index:05d}.parquet"


def chunk_stats_path(snapshot: Path, index: int) -> Path:
    return snapshot / CHUNKS_DIR / f"part-{index:05d}.json"


def atomic_write_text(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + TMP_SUFFIX)
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def atomic_write_table(path: Path, table: pa.Table) -> None:
    tmp = path.with_name(path.name + TMP_SUFFIX)
    pq.write_table(table, tmp, compression=PARQUET_COMPRESSION)
    os.replace(tmp, path)


def remove_tmp_files(directory: Path) -> None:
    for tmp in directory.rglob(f"*{TMP_SUFFIX}"):
        if tmp.is_file():
            tmp.unlink()


def swap_dir(new: Path, target: Path) -> None:
    """Replace `target` with `new` (both on the same filesystem); the old copy is removed last."""
    target.parent.mkdir(parents=True, exist_ok=True)
    old = new.with_name(new.name + ".old")
    shutil.rmtree(old, ignore_errors=True)
    if target.exists():
        os.replace(target, old)
    os.replace(new, target)
    shutil.rmtree(old, ignore_errors=True)

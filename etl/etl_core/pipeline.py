"""`run`: one crawl folder -> one Parquet snapshot (platform_name=<p>/crawl_date=<d>)."""

import json
import logging
import multiprocessing
import shutil
import time
from collections.abc import Callable, Iterable
from concurrent.futures import FIRST_COMPLETED, Future, ProcessPoolExecutor, wait
from dataclasses import dataclass
from datetime import UTC, date, datetime
from itertools import batched
from pathlib import Path
from typing import Any

from etl_core import __version__
from etl_core.crawl import CrawlFile, html_root, iter_crawl_files, load_crawl_log
from etl_core.dataset import DatasetSpec
from etl_core.layout import (
    CHUNKS_DIR,
    MANIFEST_NAME,
    RUN_NAME,
    atomic_write_text,
    remove_tmp_files,
    snapshot_dir,
)
from etl_core.sources import get_source, parser_fingerprint
from etl_core.writer import ChunkStats, completed_chunks, finalize_snapshot, process_chunk

log = logging.getLogger(__name__)


class ResumeMismatchError(ValueError):
    """An interrupted snapshot was started with other run settings, so it cannot be resumed."""


@dataclass(frozen=True, slots=True)
class RunConfig:
    dataset: DatasetSpec[Any]
    platform: str
    data_dir: Path
    out: Path
    crawl_date: date
    workers: int = 1
    chunk_size: int = 2000
    resume: bool = False


def run_snapshot(config: RunConfig) -> dict[str, Any]:
    dataset = config.dataset
    dataset.mapper(config.platform)
    source = get_source(config.platform)
    if config.chunk_size < 1:
        raise ValueError("chunk_size must be >= 1")
    if not html_root(config.data_dir).is_dir():
        raise FileNotFoundError(f"crawl folder not found: {config.data_dir}")

    snapshot = snapshot_dir(config.out, config.platform, config.crawl_date)
    run_identity = {
        "dataset": dataset.name,
        "platform": config.platform,
        "data_dir": str(config.data_dir.resolve()),
        "chunk_size": config.chunk_size,
    }
    done = _prepare_snapshot(snapshot, run_identity, config.resume)

    started = datetime.now(UTC)
    crawl_log = load_crawl_log(config.data_dir)
    log.info(
        "%s %s: %d crawl log entries, %d chunks already done, writing %s",
        config.platform,
        config.crawl_date,
        len(crawl_log),
        len(done),
        snapshot,
    )

    chunks = (
        (index, list(files))
        for index, files in enumerate(
            batched(iter_crawl_files(config.data_dir, crawl_log), config.chunk_size)
        )
        if index not in done
    )
    progress = _Progress(dataset.name)
    if config.workers <= 1:
        for index, files in chunks:
            progress(process_chunk(dataset, config.platform, index, files, str(snapshot)))
    else:
        _run_pool(dataset, config.platform, chunks, str(snapshot), config.workers, progress)

    finished = datetime.now(UTC)
    manifest = finalize_snapshot(
        snapshot,
        {
            **run_identity,
            "crawl_date": config.crawl_date.isoformat(),
            "html_root": str(html_root(config.data_dir).resolve()),
            "crawl_log_entries": len(crawl_log),
            "started_at": started.isoformat(),
            "finished_at": finished.isoformat(),
            "duration_s": round((finished - started).total_seconds(), 1),
            "resumed_chunks": len(done),
            "parser": {"path": str(source.parser_path), "sha256": parser_fingerprint(config.platform)},
            "etl_version": __version__,
        },
    )
    log.info(
        "%s %s done: %d files, %d %s rows, %d skipped, %d without id, %d errors",
        config.platform,
        config.crawl_date,
        manifest["files_total"],
        manifest["rows"],
        dataset.name,
        manifest["skipped"],
        manifest["no_id"],
        manifest["errors"],
    )
    return manifest


def _prepare_snapshot(snapshot: Path, run_identity: dict[str, Any], resume: bool) -> set[int]:
    """Fresh run: wipe the snapshot. Resume: keep finished chunks if the run settings match."""
    run_file = snapshot / RUN_NAME
    if resume and run_file.is_file():
        previous = json.loads(run_file.read_text(encoding="utf-8"))
        if previous != run_identity:
            raise ResumeMismatchError(
                f"cannot resume {snapshot}: it was started with {previous}, now {run_identity}"
            )
        (snapshot / MANIFEST_NAME).unlink(missing_ok=True)  # marks the snapshot unfinished for `compact`
        remove_tmp_files(snapshot)
        return completed_chunks(snapshot)
    shutil.rmtree(snapshot, ignore_errors=True)
    (snapshot / CHUNKS_DIR).mkdir(parents=True)
    atomic_write_text(run_file, json.dumps(run_identity))
    return set()


def _run_pool(
    dataset: DatasetSpec[Any],
    platform: str,
    chunks: Iterable[tuple[int, list[CrawlFile]]],
    snapshot: str,
    workers: int,
    on_done: Callable[[ChunkStats], None],
) -> None:
    """Keep at most 2 * workers chunks in flight so memory stays bounded on huge crawls."""
    with ProcessPoolExecutor(max_workers=workers, mp_context=_mp_context()) as pool:
        in_flight: set[Future[ChunkStats]] = set()
        for index, files in chunks:
            in_flight.add(pool.submit(process_chunk, dataset, platform, index, files, snapshot))
            if len(in_flight) >= workers * 2:
                in_flight = _drain(in_flight, on_done, FIRST_COMPLETED)
        _drain(in_flight, on_done)


def _mp_context() -> multiprocessing.context.BaseContext:
    """Not fork: the parent may already run pyarrow/duckdb threads, and forking those can deadlock."""
    method = "forkserver" if "forkserver" in multiprocessing.get_all_start_methods() else "spawn"
    return multiprocessing.get_context(method)


def _drain(
    futures: set[Future[ChunkStats]],
    on_done: Callable[[ChunkStats], None],
    return_when: str = "ALL_COMPLETED",
) -> set[Future[ChunkStats]]:
    finished, pending = wait(futures, return_when=return_when)
    for future in finished:
        on_done(future.result())
    return pending


class _Progress:
    def __init__(self, dataset_name: str) -> None:
        self.dataset_name = dataset_name
        self.t0 = time.monotonic()
        self.files = self.rows = self.errors = 0

    def __call__(self, stats: ChunkStats) -> None:
        self.files += stats.files
        self.rows += stats.rows
        self.errors += len(stats.errors)
        rate = self.files / max(time.monotonic() - self.t0, 1e-9)
        log.info(
            "chunk %05d done: %d files this run, %d %s rows, %d errors, %.0f files/s",
            stats.index,
            self.files,
            self.rows,
            self.dataset_name,
            self.errors,
            rate,
        )

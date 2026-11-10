"""`run`: one crawl folder -> MongoDB versions + conditional main updates."""

from __future__ import annotations

import logging
import multiprocessing
import time
from collections.abc import Callable, Iterable
from concurrent.futures import FIRST_COMPLETED, Future, ProcessPoolExecutor, wait
from dataclasses import dataclass
from datetime import UTC, date, datetime
from itertools import batched
from pathlib import Path
from typing import Any

from etl_core.crawl import (
    CrawlFile,
    html_root,
    iter_crawl_files,
    load_crawl_log,
    read_crawl_manifest,
)
from etl_core.dataset import DatasetSpec
from etl_core.sources import get_source, parser_fingerprint
from etl_core.store import (
    ChunkStats,
    CrawlIdentity,
    EntityStore,
    MongoConfig,
    ResumeMismatchError,
)
from etl_core.writer import process_chunk

log = logging.getLogger(__name__)

# Re-export for callers/tests that imported it from pipeline.
__all__ = ["ResumeMismatchError", "RunConfig", "run_snapshot"]


@dataclass(frozen=True, slots=True)
class RunConfig:
    dataset: DatasetSpec[Any]
    platform: str
    data_dir: Path
    crawl_date: date
    mongo: MongoConfig
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

    store = EntityStore(config.mongo)
    try:
        identity = CrawlIdentity(
            dataset=dataset.name,
            platform=config.platform,
            data_dir=str(config.data_dir.resolve()),
            chunk_size=config.chunk_size,
        )
        crawl_manifest = read_crawl_manifest(config.data_dir) or {}
        crawl_id, done = store.prepare_crawl(
            identity,
            config.crawl_date,
            resume=config.resume,
            parser={"path": str(source.parser_path), "sha256": parser_fingerprint(config.platform)},
            crawl_mode=crawl_manifest.get("crawl_mode"),
            crawl_status=crawl_manifest.get("status"),
        )

        started = datetime.now(UTC)
        crawl_log = load_crawl_log(config.data_dir)
        log.info(
            "%s %s: %d crawl log entries, %d chunks already done, crawl_id=%s",
            config.platform,
            config.crawl_date,
            len(crawl_log),
            len(done),
            crawl_id,
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
                progress(
                    process_chunk(
                        dataset,
                        config.platform,
                        index,
                        files,
                        crawl_id,
                        config.crawl_date,
                        config.mongo.uri,
                        config.mongo.database,
                    )
                )
        else:
            _run_pool(
                dataset,
                config.platform,
                chunks,
                crawl_id,
                config.crawl_date,
                config.mongo.uri,
                config.mongo.database,
                config.workers,
                progress,
            )

        ledger = store.get_crawl(crawl_id) or {}
        chunk_stats = list(ledger.get("chunk_stats") or [])
        run_stats = {
            "chunks": len(ledger.get("completed_chunks") or []),
            "files_total": sum(int(s.get("files", 0)) for s in chunk_stats),
            "skipped": sum(int(s.get("skipped", 0)) for s in chunk_stats),
            "no_id": sum(int(s.get("no_id", 0)) for s in chunk_stats),
            "resumed_chunks": len(done),
            "started_at": started,
            "html_root": str(html_root(config.data_dir).resolve()),
            "crawl_log_entries": len(crawl_log),
        }

        manifest = store.finalize_crawl(
            crawl_id,
            dataset.name,
            config.platform,
            config.crawl_date,
            dataset.within_crawl_order,
            parser_fingerprint=parser_fingerprint(config.platform),
            run_info=run_stats,
        )
        log.info(
            "%s %s done: %d files, %d %s rows, %d skipped, %d without id, %d errors",
            config.platform,
            config.crawl_date,
            manifest.get("files_total", 0),
            manifest.get("rows", 0),
            dataset.name,
            manifest.get("skipped", 0),
            manifest.get("no_id", 0),
            manifest.get("errors", 0),
        )
        return manifest
    except Exception as exc:
        try:
            crawl_id_exc = crawl_ledger_id_safe(config)
            if crawl_id_exc:
                store.mark_failed(crawl_id_exc, str(exc))
        except Exception:
            log.exception("failed to mark crawl as failed")
        raise
    finally:
        store.close()


def crawl_ledger_id_safe(config: RunConfig) -> str | None:
    from etl_core.documents import crawl_date_str, crawl_ledger_id

    try:
        return crawl_ledger_id(config.dataset.name, config.platform, crawl_date_str(config.crawl_date))
    except Exception:
        return None


def _run_pool(
    dataset: DatasetSpec[Any],
    platform: str,
    chunks: Iterable[tuple[int, list[CrawlFile]]],
    crawl_id: str,
    crawl_date: date,
    mongo_uri: str,
    mongo_db: str,
    workers: int,
    on_done: Callable[[ChunkStats], None],
) -> None:
    """Keep at most 2 * workers chunks in flight so memory stays bounded on huge crawls."""
    with ProcessPoolExecutor(max_workers=workers, mp_context=_mp_context()) as pool:
        in_flight: set[Future[ChunkStats]] = set()
        for index, files in chunks:
            in_flight.add(
                pool.submit(
                    process_chunk,
                    dataset,
                    platform,
                    index,
                    files,
                    crawl_id,
                    crawl_date,
                    mongo_uri,
                    mongo_db,
                )
            )
            if len(in_flight) >= workers * 2:
                in_flight = _drain(in_flight, on_done, FIRST_COMPLETED)
        _drain(in_flight, on_done)


def _mp_context() -> multiprocessing.context.BaseContext:
    """Not fork: the parent may already hold Mongo/pyarrow threads, and forking those can deadlock."""
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
        self.files = self.rows = self.errors = self.skipped = self.no_id = 0

    def __call__(self, stats: ChunkStats) -> None:
        self.files += stats.files
        self.rows += stats.rows
        self.errors += len(stats.errors or [])
        self.skipped += stats.skipped
        self.no_id += stats.no_id
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

"""Shared CLI for every dataset (`artist-etl`, `artwork-etl`, `lot-etl`)."""

from __future__ import annotations

import argparse
import logging
import os
from datetime import date
from pathlib import Path
from typing import Any

from etl_core.compact import compact
from etl_core.crawl import detect_crawl_date
from etl_core.dataset import DatasetSpec
from etl_core.migrate import migrate_parquet
from etl_core.pipeline import RunConfig, run_snapshot
from etl_core.private_sales import MIN_RATIO, detect_private_sales
from etl_core.store import DEFAULT_DATABASE, DEFAULT_URI, EntityStore, MongoConfig
from etl_core.sync import SyncConfig, sync

USAGE = """\
    {prog} run --platform saatchi --data /path/to/saatchi/2026-09-23 [--crawl-date D] [--workers N]
               [--chunk-size N] [--resume] [--mongo-uri URI] [--mongo-db NAME]
    {prog} compact --platform saatchi [--mongo-uri URI] [--mongo-db NAME]
    {prog} sync --data-root /path/to/html-downloader/data [--platform P ...] [--workers N]
                [--chunk-size N] [--include-unmanaged] [--dry-run] [--mongo-uri URI] [--mongo-db NAME]
    {prog} migrate --from /path/to/output/artists [--platform P ...] [--mongo-uri URI] [--mongo-db NAME]
    {prog} private-sales --platform saatchi --previous 2026-10-05 --current 2026-11-02 [--min-ratio 0.8]
                         [--dry-run] [--mongo-uri URI] [--mongo-db NAME]     (artwork-etl only)

(`python -m {package} ...` from the etl/ folder is equivalent.)
"""


def build_parser(dataset: DatasetSpec[Any]) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=dataset.prog,
        description=USAGE.format(prog=dataset.prog, package=dataset.ref.partition(".")[0]),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="parse one crawl folder into MongoDB versions")
    run.add_argument("--platform", required=True, choices=dataset.platforms)
    run.add_argument("--data", required=True, help="crawl folder (an html/ subfolder is used if present)")
    run.add_argument("--crawl-date", help="YYYY-MM-DD; default: inferred from the crawl folder name")
    _add_processing_args(run)
    _add_mongo_args(run)
    run.add_argument("--resume", action="store_true", help="keep finished chunks of an interrupted run")

    comp = sub.add_parser("compact", help="rebuild main documents for a platform from versions")
    comp.add_argument("--platform", required=True, choices=dataset.platforms)
    _add_mongo_args(comp)

    syn = sub.add_parser("sync", help="process every new finished crawl folder, then compact")
    syn.add_argument("--data-root", required=True, help="downloader data folder: <root>/<platform>/<date>/")
    syn.add_argument(
        "--platform",
        action="append",
        choices=dataset.platforms,
        help="repeatable; default: every platform",
    )
    _add_processing_args(syn)
    _add_mongo_args(syn)
    syn.add_argument(
        "--include-unmanaged",
        action="store_true",
        help="also process crawl folders without a downloader manifest.json",
    )
    syn.add_argument("--dry-run", action="store_true", help="only list the crawl folders to process")

    mig = sub.add_parser("migrate", help="import existing Parquet snapshots into MongoDB")
    mig.add_argument(
        "--from",
        dest="from_dir",
        required=True,
        help="old dataset output root containing snapshots/platform_name=*/crawl_date=*/*.parquet",
    )
    mig.add_argument(
        "--platform",
        action="append",
        choices=dataset.platforms,
        help="repeatable; default: every platform found under snapshots/",
    )
    _add_mongo_args(mig)

    if dataset.private_sales_dataset == dataset.name:
        ps = sub.add_parser(
            "private-sales",
            help="record what was in one full crawl and is gone from a later full crawl",
        )
        ps.add_argument("--platform", required=True, choices=dataset.platforms)
        ps.add_argument("--previous", required=True, help="YYYY-MM-DD of the earlier full crawl")
        ps.add_argument("--current", required=True, help="YYYY-MM-DD of the later full crawl")
        ps.add_argument(
            "--min-ratio",
            type=float,
            default=MIN_RATIO,
            help="record nothing if the later crawl has fewer than this share of the earlier one's rows",
        )
        ps.add_argument("--dry-run", action="store_true", help="only count, write nothing")
        _add_mongo_args(ps)
    return parser


def _add_processing_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--workers", type=int, default=max((os.cpu_count() or 2) - 1, 1))
    parser.add_argument("--chunk-size", type=int, default=2000, help="HTML files per worker chunk")


def _add_mongo_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--mongo-uri",
        default=None,
        help=f"MongoDB URI (default: $MONGODB_URI or {DEFAULT_URI})",
    )
    parser.add_argument(
        "--mongo-db",
        default=None,
        help=f"MongoDB database name (default: $MONGODB_DATABASE or {DEFAULT_DATABASE})",
    )


def _mongo_from_args(args: argparse.Namespace) -> MongoConfig:
    return MongoConfig.from_env(uri=args.mongo_uri, database=args.mongo_db)


def main(dataset: DatasetSpec[Any], argv: list[str] | None = None) -> int:
    args = build_parser(dataset).parse_args(argv)
    logging.basicConfig(level=args.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        mongo = _mongo_from_args(args)
        if args.command == "run":
            data_dir = Path(args.data).expanduser()
            run_snapshot(
                RunConfig(
                    dataset=dataset,
                    platform=args.platform,
                    data_dir=data_dir,
                    crawl_date=detect_crawl_date(data_dir, args.crawl_date),
                    mongo=mongo,
                    workers=args.workers,
                    chunk_size=args.chunk_size,
                    resume=args.resume,
                )
            )
        elif args.command == "compact":
            compact(dataset, args.platform, mongo)
        elif args.command == "private-sales":
            store = EntityStore(mongo)
            try:
                detect_private_sales(
                    store,
                    dataset.name,
                    args.platform,
                    date.fromisoformat(args.previous),
                    date.fromisoformat(args.current),
                    min_ratio=args.min_ratio,
                    dry_run=args.dry_run,
                )
            finally:
                store.close()
        elif args.command == "migrate":
            migrate_parquet(
                dataset,
                Path(args.from_dir).expanduser(),
                mongo,
                platforms=tuple(args.platform or ()),
            )
        else:
            result = sync(
                dataset,
                SyncConfig(
                    data_root=Path(args.data_root).expanduser(),
                    mongo=mongo,
                    platforms=tuple(args.platform or ()),
                    workers=args.workers,
                    chunk_size=args.chunk_size,
                    include_unmanaged=args.include_unmanaged,
                    dry_run=args.dry_run,
                ),
            )
            return 0 if result.ok else 1
    except (ValueError, FileNotFoundError) as exc:
        logging.getLogger(dataset.prog).error("%s", exc)
        return 2
    return 0

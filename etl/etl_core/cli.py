"""Shared CLI for every dataset (`artist-etl`, `artwork-etl`)."""

import argparse
import logging
import os
from pathlib import Path
from typing import Any

from etl_core.compact import compact
from etl_core.crawl import detect_crawl_date
from etl_core.dataset import DatasetSpec
from etl_core.pipeline import RunConfig, run_snapshot
from etl_core.sync import SyncConfig, sync

USAGE = """\
    {prog} run --platform saatchi --data /path/to/saatchi/2026-09-23 [--crawl-date D] [--workers N]
               [--chunk-size N] [--resume] [--out {out}]
    {prog} compact --platform saatchi [--out {out}]
    {prog} sync --data-root /path/to/html-downloader/data [--platform P ...] [--workers N]
                [--chunk-size N] [--include-unmanaged] [--dry-run] [--out {out}]

(`python -m {package} ...` from the etl/ folder is equivalent.)
"""


def build_parser(dataset: DatasetSpec[Any]) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=dataset.prog,
        description=USAGE.format(
            prog=dataset.prog, out=dataset.default_out, package=dataset.ref.partition(".")[0]
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="parse one crawl folder into a Parquet snapshot")
    run.add_argument("--platform", required=True, choices=dataset.platforms)
    run.add_argument("--data", required=True, help="crawl folder (an html/ subfolder is used if present)")
    run.add_argument("--crawl-date", help="YYYY-MM-DD; default: inferred from the crawl folder name")
    _add_processing_args(run)
    run.add_argument("--resume", action="store_true", help="keep finished chunks of an interrupted run")
    run.add_argument("--out", default=dataset.default_out)

    comp = sub.add_parser("compact", help="dedup snapshots and rebuild current/ for a platform")
    comp.add_argument("--platform", required=True, choices=dataset.platforms)
    comp.add_argument("--out", default=dataset.default_out)

    syn = sub.add_parser("sync", help="snapshot every new finished crawl folder, then compact")
    syn.add_argument("--data-root", required=True, help="downloader data folder: <root>/<platform>/<date>/")
    syn.add_argument(
        "--platform",
        action="append",
        choices=dataset.platforms,
        help="repeatable; default: every platform",
    )
    _add_processing_args(syn)
    syn.add_argument(
        "--include-unmanaged",
        action="store_true",
        help="also process crawl folders without a downloader manifest.json",
    )
    syn.add_argument("--dry-run", action="store_true", help="only list the crawl folders to process")
    syn.add_argument("--out", default=dataset.default_out)
    return parser


def _add_processing_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--workers", type=int, default=max((os.cpu_count() or 2) - 1, 1))
    parser.add_argument("--chunk-size", type=int, default=2000, help="HTML files per Parquet part file")


def main(dataset: DatasetSpec[Any], argv: list[str] | None = None) -> int:
    args = build_parser(dataset).parse_args(argv)
    logging.basicConfig(level=args.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        if args.command == "run":
            data_dir = Path(args.data).expanduser()
            run_snapshot(
                RunConfig(
                    dataset=dataset,
                    platform=args.platform,
                    data_dir=data_dir,
                    out=Path(args.out).expanduser(),
                    crawl_date=detect_crawl_date(data_dir, args.crawl_date),
                    workers=args.workers,
                    chunk_size=args.chunk_size,
                    resume=args.resume,
                )
            )
        elif args.command == "compact":
            compact(dataset, args.platform, Path(args.out).expanduser())
        else:
            result = sync(
                dataset,
                SyncConfig(
                    data_root=Path(args.data_root).expanduser(),
                    out=Path(args.out).expanduser(),
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

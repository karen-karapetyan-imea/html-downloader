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

USAGE = """\
    {prog} run --platform saatchi --data /path/to/saatchi/2026-09-23 [--crawl-date D] [--workers N]
               [--chunk-size N] [--resume] [--out {out}]
    {prog} compact --platform saatchi [--out {out}]

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
    run.add_argument("--workers", type=int, default=max((os.cpu_count() or 2) - 1, 1))
    run.add_argument("--chunk-size", type=int, default=2000, help="HTML files per Parquet part file")
    run.add_argument("--resume", action="store_true", help="keep finished chunks of an interrupted run")
    run.add_argument("--out", default=dataset.default_out)

    comp = sub.add_parser("compact", help="dedup snapshots and rebuild current/ for a platform")
    comp.add_argument("--platform", required=True, choices=dataset.platforms)
    comp.add_argument("--out", default=dataset.default_out)
    return parser


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
        else:
            compact(dataset, args.platform, Path(args.out).expanduser())
    except (ValueError, FileNotFoundError) as exc:
        logging.getLogger(dataset.prog).error("%s", exc)
        return 2
    return 0

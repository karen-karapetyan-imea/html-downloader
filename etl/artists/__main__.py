"""CLI.

    artist-etl run --platform saatchi --data /path/to/saatchi/2026-09-23 [--crawl-date D] [--workers N]
                   [--chunk-size N] [--resume] [--out output/artists]
    artist-etl compact --platform saatchi [--out output/artists]

(`python -m artists ...` from the etl/ folder is equivalent.)
"""

import argparse
import logging
import os
import sys
from pathlib import Path

from artists.compact import compact
from artists.crawl import detect_crawl_date
from artists.pipeline import RunConfig, run_snapshot
from artists.sources import PLATFORMS

DEFAULT_OUT = "output/artists"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="artist-etl", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="parse one crawl folder into a Parquet snapshot")
    run.add_argument("--platform", required=True, choices=PLATFORMS)
    run.add_argument("--data", required=True, help="crawl folder (an html/ subfolder is used if present)")
    run.add_argument("--crawl-date", help="YYYY-MM-DD; default: inferred from the crawl folder name")
    run.add_argument("--workers", type=int, default=max((os.cpu_count() or 2) - 1, 1))
    run.add_argument("--chunk-size", type=int, default=2000, help="HTML files per Parquet part file")
    run.add_argument("--resume", action="store_true", help="keep finished chunks of an interrupted run")
    run.add_argument("--out", default=DEFAULT_OUT)

    comp = sub.add_parser("compact", help="dedup snapshots and rebuild current/ for a platform")
    comp.add_argument("--platform", required=True, choices=PLATFORMS)
    comp.add_argument("--out", default=DEFAULT_OUT)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=args.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        if args.command == "run":
            data_dir = Path(args.data).expanduser()
            run_snapshot(
                RunConfig(
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
            compact(args.platform, Path(args.out).expanduser())
    except (ValueError, FileNotFoundError) as exc:
        logging.getLogger("artist-etl").error("%s", exc)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

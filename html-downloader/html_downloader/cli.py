"""CLI: discover sitemap URLs and download HTML into dated job folders."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from html_downloader.discover.service import run_discover
from html_downloader.download.service import ProxyRequiredError, run_download
from html_downloader.paths import DEFAULT_DATA_ROOT, DEFAULT_STATE_ROOT, MARKETPLACES, parse_crawl_date

LOGGER = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Sitemap discovery and stealth HTML download (proxies required for download)."
    )
    sub = parser.add_subparsers(dest="command")

    discover = sub.add_parser("discover", help="Fetch entity URLs from marketplace sitemaps")
    _add_shared_job_flags(discover)
    discover.add_argument(
        "--state-root",
        type=Path,
        default=DEFAULT_STATE_ROOT,
        help="Directory for lastmod state JSON (default: ./state)",
    )
    discover.add_argument(
        "--incremental",
        action="store_true",
        help="Only emit new/updated URLs using lastmod + prior results.jsonl",
    )
    discover.add_argument(
        "--no-updates",
        action="store_true",
        help="When incremental, skip lastmod updates (new entities only)",
    )
    discover.add_argument(
        "--update-state",
        action="store_true",
        help="Refresh lastmod state after a successful sitemap fetch",
    )
    discover.add_argument(
        "--proxy-file",
        default=None,
        help="Proxy list (required for Artsy). Format: host:port:user:pass",
    )
    discover.add_argument("--concurrency", type=int, default=None, help="Parallel sitemap fetches")
    discover.add_argument("--dry-run", action="store_true", help="Fetch + log only; do not write job files")
    discover.set_defaults(func=_cmd_discover)

    download = sub.add_parser("download", help="Download HTML for a dated job folder")
    _add_shared_job_flags(download)
    download.add_argument(
        "--proxy-file",
        required=True,
        help="Proxy list file (host:port:user:pass per line). Required.",
    )
    download.add_argument("--urls", type=Path, default=None, help="Override job urls.txt")
    download.add_argument("--workers", type=int, default=None, help="Max worker threads (cap 64)")
    download.add_argument("--rps", type=float, default=None, help="Target requests per second")
    download.add_argument(
        "--skip-existing",
        action="store_true",
        help="Skip fetch when this job's HTML file already exists",
    )
    download.add_argument(
        "--no-results-append",
        action="store_true",
        help="Truncate results.jsonl before crawl",
    )
    download.set_defaults(func=_cmd_download)

    return parser


def _add_shared_job_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--marketplace",
        required=True,
        choices=MARKETPLACES,
        help="Marketplace to crawl",
    )
    parser.add_argument("--date", default=None, help="Job date YYYY-MM-DD (default: UTC today)")
    parser.add_argument(
        "--data-root",
        type=Path,
        default=DEFAULT_DATA_ROOT,
        help="Root for dated job folders (default: ./data)",
    )


def _cmd_discover(args: argparse.Namespace) -> int:
    try:
        result = run_discover(
            marketplace=args.marketplace,
            data_root=args.data_root,
            state_root=args.state_root,
            crawl_date=parse_crawl_date(args.date),
            incremental=args.incremental,
            include_updates=not args.no_updates,
            update_state=args.update_state,
            proxy_file=args.proxy_file,
            concurrency=args.concurrency,
            dry_run=args.dry_run,
        )
    except ValueError as exc:
        LOGGER.error("%s", exc)
        return 1
    print(f"Discover finished job={result.job} all={result.all_count} to_crawl={result.crawl_count}")
    return 0


def _cmd_download(args: argparse.Namespace) -> int:
    try:
        result = run_download(
            marketplace=args.marketplace,
            data_root=args.data_root,
            crawl_date=parse_crawl_date(args.date),
            proxy_file=args.proxy_file,
            urls_override=args.urls,
            max_workers=args.workers,
            requests_per_second=args.rps,
            skip_existing=args.skip_existing,
            results_append=not args.no_results_append,
        )
    except (ProxyRequiredError, FileNotFoundError, ValueError) as exc:
        LOGGER.error("%s", exc)
        return 1
    print(f"Download finished job={result.job} urls={result.url_count} status={result.status}")
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 2
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())

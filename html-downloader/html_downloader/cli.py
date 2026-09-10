"""CLI: discover sitemap URLs and download HTML into dated job folders."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from html_downloader.auctions.paths import AUCTION_HOUSES
from html_downloader.auctions.service import run_auction_discover, run_auction_download
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

    auction = sub.add_parser("auction", help="Monthly auction-house discovery and download")
    auction_sub = auction.add_subparsers(dest="auction_command", required=True)

    auction_discover = auction_sub.add_parser(
        "discover",
        help="Fetch auction event URLs for an auction house",
    )
    _add_auction_job_flags(auction_discover)
    auction_discover.add_argument(
        "--state-root",
        type=Path,
        default=DEFAULT_STATE_ROOT,
        help="Root for auction state (uses state/auctions/; default: ./state)",
    )
    auction_discover.add_argument(
        "--incremental",
        action="store_true",
        help="Only emit new/updated auction URLs using lastmod + prior results.jsonl",
    )
    auction_discover.add_argument(
        "--no-updates",
        action="store_true",
        help="When incremental, skip lastmod updates (new auctions only)",
    )
    auction_discover.add_argument(
        "--update-state",
        action="store_true",
        help="Refresh auction lastmod state after a successful sitemap fetch",
    )
    auction_discover.add_argument(
        "--proxy-file",
        default=None,
        help="Proxy list if needed. Format: host:port:user:pass",
    )
    auction_discover.add_argument(
        "--concurrency",
        type=int,
        default=None,
        help="Parallel sitemap fetches",
    )
    auction_discover.add_argument(
        "--expand-algolia",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Browse Algolia archive_prod by year for historical lots "
            "(full archive; default: on)"
        ),
    )
    auction_discover.add_argument(
        "--algolia-from-year",
        type=int,
        default=None,
        help="Earliest Algolia sale year (default: 1989)",
    )
    auction_discover.add_argument(
        "--algolia-to-year",
        type=int,
        default=None,
        help="Latest Algolia sale year (default: current UTC year)",
    )
    auction_discover.add_argument(
        "--algolia-workers",
        type=int,
        default=2,
        help="Year partitions to browse concurrently (default: 2)",
    )
    auction_discover.add_argument(
        "--algolia-delay",
        type=float,
        default=0.4,
        help="Seconds between Algolia browse calls per worker (default: 0.4)",
    )
    auction_discover.add_argument(
        "--algolia-force",
        action="store_true",
        help="Reset Algolia browse state and lot cache, then re-walk all years",
    )
    auction_discover.add_argument(
        "--expand-artist-sold",
        action=argparse.BooleanOptionalAction,
        default=False,
        help=(
            "After XML lot/catalog discovery, paginate sitemap_artist_sold_* pages "
            "for historical lots (default: off; prefer --expand-algolia)"
        ),
    )
    auction_discover.add_argument(
        "--artist-sold-concurrency",
        type=int,
        default=4,
        help="Parallel artist sold-page workers (default: 4)",
    )
    auction_discover.add_argument(
        "--max-artist-sold",
        type=int,
        default=None,
        help="Limit artist sold seeds (smoke/debug); default: all ~75k",
    )
    auction_discover.add_argument(
        "--include-hubs",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Also ingest auction-house / artists / category XML sitemaps "
            "(soft-fail; default: on)"
        ),
    )
    auction_discover.add_argument(
        "--expand-auctions-list",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Paginate /auctions/ SSR listing for upcoming catalogs (default: on)",
    )
    auction_discover.add_argument(
        "--expand-houses",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Walk auction-house pages for extra catalog/lot links (default: off)",
    )
    auction_discover.add_argument(
        "--house-expand-concurrency",
        type=int,
        default=2,
        help="Parallel house-page workers when --expand-houses (default: 2)",
    )
    auction_discover.add_argument(
        "--max-houses",
        type=int,
        default=None,
        help="Limit house pages crawled when --expand-houses (smoke/debug)",
    )
    auction_discover.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch + log only; do not write job files",
    )
    auction_discover.set_defaults(func=_cmd_auction_discover)

    auction_download = auction_sub.add_parser(
        "download",
        help="Download HTML for a monthly auction job folder",
    )
    _add_auction_job_flags(auction_download)
    auction_download.add_argument(
        "--proxy-file",
        required=True,
        help="Proxy list file (host:port:user:pass per line). Required.",
    )
    auction_download.add_argument("--urls", type=Path, default=None, help="Override job urls.txt")
    auction_download.add_argument("--workers", type=int, default=None, help="Max worker threads (cap 64)")
    auction_download.add_argument("--rps", type=float, default=None, help="Target requests per second")
    auction_download.add_argument(
        "--skip-existing",
        action="store_true",
        help="Skip fetch when this job's HTML file already exists",
    )
    auction_download.add_argument(
        "--no-results-append",
        action="store_true",
        help="Truncate results.jsonl before crawl",
    )
    auction_download.set_defaults(func=_cmd_auction_download)

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


def _add_auction_job_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--auction-house",
        required=True,
        choices=AUCTION_HOUSES,
        help="Auction house to crawl",
    )
    parser.add_argument(
        "--month",
        default=None,
        help="Job month YYYY-MM (default: current UTC month)",
    )
    parser.add_argument(
        "--data-root",
        type=Path,
        default=DEFAULT_DATA_ROOT,
        help="Root for auction job folders under data/auctions/ (default: ./data)",
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


def _cmd_auction_discover(args: argparse.Namespace) -> int:
    try:
        result = run_auction_discover(
            auction_house=args.auction_house,
            data_root=args.data_root,
            state_root=args.state_root,
            job_month=args.month,
            incremental=args.incremental,
            include_updates=not args.no_updates,
            update_state=args.update_state,
            proxy_file=args.proxy_file,
            concurrency=args.concurrency,
            dry_run=args.dry_run,
            expand_artist_sold=args.expand_artist_sold,
            artist_sold_concurrency=args.artist_sold_concurrency,
            max_artist_sold=args.max_artist_sold,
            expand_algolia=args.expand_algolia,
            algolia_from_year=args.algolia_from_year,
            algolia_to_year=args.algolia_to_year,
            algolia_workers=args.algolia_workers,
            algolia_delay=args.algolia_delay,
            algolia_force=args.algolia_force,
            include_hubs=args.include_hubs,
            expand_auctions_list=args.expand_auctions_list,
            expand_houses=args.expand_houses,
            house_expand_concurrency=args.house_expand_concurrency,
            max_houses=args.max_houses,
        )
    except (ValueError, RuntimeError) as exc:
        LOGGER.error("%s", exc)
        return 1
    print(
        f"Auction discover finished job={result.job} month={result.job_month} "
        f"all={result.all_count} to_crawl={result.crawl_count}"
    )
    return 0


def _cmd_auction_download(args: argparse.Namespace) -> int:
    try:
        result = run_auction_download(
            auction_house=args.auction_house,
            data_root=args.data_root,
            job_month=args.month,
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
    print(
        f"Auction download finished job={result.job} month={result.job_month} "
        f"urls={result.url_count} status={result.status}"
    )
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

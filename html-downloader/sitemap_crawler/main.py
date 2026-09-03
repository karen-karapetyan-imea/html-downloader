"""CLI entry point for the 1stDibs art HTML sitemap crawler."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from sitemap_crawler.crawler import CrawlConfig, CrawlResult, SitemapCrawler
from sitemap_crawler.storage import CrawlerState, write_art_outputs

LOGGER = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Crawl 1stDibs art HTML sitemap branches and extract artwork, dealer, "
            "and creator URLs."
        ),
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.5,
        help="Seconds to wait between request batches (default: 0.5)",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=10,
        help="Concurrent sitemap page fetches (default: 10)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="HTTP request timeout in seconds (default: 30)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("output"),
        help="Directory for artwork/dealer/creator URL output files (default: output)",
    )
    parser.add_argument(
        "--state-dir",
        type=Path,
        default=Path("state/sitemap_crawler"),
        help="Directory for resume state (default: state/sitemap_crawler)",
    )
    parser.add_argument(
        "--reset-state",
        action="store_true",
        help="Clear resume state before starting",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=None,
        help="Stop after crawling this many art sitemap pages (for testing)",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level (default: INFO)",
    )
    return parser


def print_stats(result: CrawlResult, *, output_paths: dict[str, Path]) -> None:
    print(f"Art sitemap pages visited: {result.art_sitemap_pages_visited}")
    print(f"Pending sitemap pages: {result.pending_sitemap_pages}")
    print()
    print(f"Artwork URLs: {result.artwork_urls}")
    print(f"Dealer URLs: {result.dealer_urls}")
    print(f"Creator URLs: {result.creator_urls}")
    print()
    print(f"Total unique URLs: {result.total_unique_urls}")
    print()
    print("Output:")
    for key in ("artwork", "dealer", "creator", "all"):
        print(output_paths[key])
    if result.interrupted:
        print()
        print("Crawl interrupted. Re-run the same command to resume.")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(message)s",
    )

    state = CrawlerState(args.state_dir)
    if args.reset_state:
        state.reset()
    else:
        state.load()

    config = CrawlConfig(
        delay=args.delay,
        timeout=args.timeout,
        max_pages=args.max_pages,
        concurrency=args.concurrency,
    )
    crawler = SitemapCrawler(state=state, config=config)

    try:
        result = crawler.run()
    except KeyboardInterrupt:
        state.save()
        LOGGER.warning("interrupted; state saved to %s", args.state_dir)
        result = CrawlResult(
            art_sitemap_pages_visited=state.art_sitemap_pages_visited(),
            pending_sitemap_pages=len(state.pending_sitemap_pages),
            artwork_urls=len(state.artwork_urls),
            dealer_urls=len(state.dealer_urls),
            creator_urls=len(state.creator_urls),
            total_unique_urls=state.total_unique_urls(),
            interrupted=True,
        )

    output_paths = write_art_outputs(
        args.output_dir,
        artwork_urls=state.artwork_urls,
        dealer_urls=state.dealer_urls,
        creator_urls=state.creator_urls,
    )
    print_stats(result, output_paths=output_paths)
    return 130 if result.interrupted else 0


if __name__ == "__main__":
    sys.exit(main())

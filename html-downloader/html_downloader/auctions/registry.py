"""Auction house registry."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from html_downloader.auctions.base import AuctionSpec
from html_downloader.auctions.invaluable import DEFAULT_INVALUABLE_INDEX
from html_downloader.auctions.paths import AUCTION_HOUSES
from html_downloader.discover.sitemap import SitemapEntry

AUCTIONS: dict[str, AuctionSpec] = {
    "invaluable": AuctionSpec(
        name="invaluable",
        default_indexes=(DEFAULT_INVALUABLE_INDEX,),
        # Sequential only — parallel lot sitemap fetches trigger WAF 202 storms.
        default_concurrency=1,
        uses_stealth_proxy=True,
    ),
    # future:
    # "sothebys": AuctionSpec(...),
    # "christies": AuctionSpec(...),
}


def get_auction(name: str) -> AuctionSpec:
    if name not in AUCTIONS:
        allowed = ", ".join(AUCTION_HOUSES)
        raise ValueError(f"unknown auction house {name!r}; expected one of: {allowed}")
    return AUCTIONS[name]


def fetch_auction_entries(
    spec: AuctionSpec,
    *,
    concurrency: int,
    proxy: dict[str, str] | None = None,
    proxies: list[dict[str, str]] | None = None,
    expand_artist_sold: bool = False,
    artist_sold_concurrency: int = 4,
    max_artist_sold: int | None = None,
    artist_sold_progress_path: Path | None = None,
    expand_algolia: bool = True,
    algolia_state_path: Path | None = None,
    algolia_from_year: int | None = None,
    algolia_to_year: int | None = None,
    algolia_workers: int = 2,
    algolia_delay: float = 0.4,
    algolia_force: bool = False,
    include_hubs: bool = True,
    expand_auctions_list: bool = True,
    expand_houses: bool = False,
    house_expand_concurrency: int = 2,
    max_houses: int | None = None,
    house_expand_progress_path: Path | None = None,
) -> list[SitemapEntry]:
    """Dispatch discovery for the given auction house."""
    if spec.name == "invaluable":
        from html_downloader.auctions.invaluable import fetch_invaluable_sitemap_entries

        kwargs: dict[str, Any] = {
            "concurrency": concurrency,
            "expand_artist_sold": expand_artist_sold,
            "artist_sold_concurrency": artist_sold_concurrency,
            "max_artist_sold": max_artist_sold,
            "artist_sold_progress_path": artist_sold_progress_path,
            "expand_algolia": expand_algolia,
            "algolia_state_path": algolia_state_path,
            "algolia_from_year": algolia_from_year,
            "algolia_to_year": algolia_to_year,
            "algolia_workers": algolia_workers,
            "algolia_delay": algolia_delay,
            "algolia_force": algolia_force,
            "include_hubs": include_hubs,
            "expand_auctions_list": expand_auctions_list,
            "expand_houses": expand_houses,
            "house_expand_concurrency": house_expand_concurrency,
            "max_houses": max_houses,
            "house_expand_progress_path": house_expand_progress_path,
        }
        if proxies:
            kwargs["proxies"] = proxies
        elif proxy is not None:
            kwargs["proxy"] = proxy
            kwargs["proxies"] = [proxy]
        return fetch_invaluable_sitemap_entries(spec.default_indexes[0], **kwargs)
    raise ValueError(f"no discovery implementation for auction house {spec.name!r}")

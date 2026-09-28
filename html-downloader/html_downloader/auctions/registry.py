"""Auction house registry."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from html_downloader.auctions.artcurial import DEFAULT_ARTCURIAL_INDEX
from html_downloader.auctions.base import AuctionSpec
from html_downloader.auctions.barnebys import DEFAULT_BARNEBYS_INDEX
from html_downloader.auctions.christies import DEFAULT_CHRISTIES_INDEX
from html_downloader.auctions.invaluable import DEFAULT_INVALUABLE_INDEX
from html_downloader.auctions.liveauctioneers import (
    DEFAULT_LIVEAUCTIONEERS_INDEX,
    DEFAULT_MAX_SITEMAPS,
    DEFAULT_MIN_URLS,
)
from html_downloader.auctions.paths import AUCTION_HOUSES
from html_downloader.auctions.drouot import (
    DEFAULT_DROUOT_LOT_INDEX,
    DEFAULT_DROUOT_SALE_INDEX,
)
from html_downloader.auctions.saleroom import DEFAULT_SALEROOM_LOTS_INDEX
from html_downloader.discover.sitemap import SitemapEntry

LOGGER = logging.getLogger(__name__)

AUCTIONS: dict[str, AuctionSpec] = {
    "invaluable": AuctionSpec(
        name="invaluable",
        default_indexes=(DEFAULT_INVALUABLE_INDEX,),
        # Sequential only — parallel lot sitemap fetches trigger WAF 202 storms.
        default_concurrency=1,
        uses_stealth_proxy=True,
    ),
    "liveauctioneers": AuctionSpec(
        name="liveauctioneers",
        default_indexes=(DEFAULT_LIVEAUCTIONEERS_INDEX,),
        # Sequential only — Imperva rate-limits parallel sitemap fetches.
        default_concurrency=1,
        uses_stealth_proxy=True,
    ),
    "artcurial": AuctionSpec(
        name="artcurial",
        default_indexes=(DEFAULT_ARTCURIAL_INDEX,),
        # Public /ace JSON API — mild parallelism is fine.
        default_concurrency=4,
        uses_stealth_proxy=False,
    ),
    "barnebys": AuctionSpec(
        name="barnebys",
        default_indexes=(DEFAULT_BARNEBYS_INDEX,),
        # Sequential only — Azure WAF + large gzipped lot sitemaps.
        default_concurrency=1,
        uses_stealth_proxy=True,
    ),
    "saleroom": AuctionSpec(
        name="saleroom",
        default_indexes=(DEFAULT_SALEROOM_LOTS_INDEX,),
        # Algolia discover needs no proxy; lot HTML download still uses --proxy-file.
        default_concurrency=1,
        uses_stealth_proxy=False,
    ),
    "drouot": AuctionSpec(
        name="drouot",
        default_indexes=(DEFAULT_DROUOT_LOT_INDEX, DEFAULT_DROUOT_SALE_INDEX),
        # Sitemap/search discover needs no proxy; lot HTML may hit Cloudflare.
        default_concurrency=1,
        uses_stealth_proxy=False,
    ),
    "christies": AuctionSpec(
        name="christies",
        default_indexes=(DEFAULT_CHRISTIES_INDEX,),
        # Sequential sitemap walk (Akamai); proxies are fallback-only for XML.
        default_concurrency=1,
        uses_stealth_proxy=False,
    ),
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
    algolia_supercategories: Sequence[str] | None = None,
    include_hubs: bool = True,
    expand_auctions_list: bool = True,
    expand_houses: bool = False,
    house_expand_concurrency: int = 2,
    max_houses: int | None = None,
    house_expand_progress_path: Path | None = None,
    max_sitemaps: int = DEFAULT_MAX_SITEMAPS,
    min_urls: int = DEFAULT_MIN_URLS,
    sitemap_progress_path: Path | None = None,
    sitemap_force: bool = False,
    max_sales: int | None = None,
    sales_progress_path: Path | None = None,
    expand_art_categories: bool = True,
    art_progress_path: Path | None = None,
    art_force: bool = False,
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
            "algolia_supercategories": algolia_supercategories,
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

    if spec.name == "liveauctioneers":
        from html_downloader.auctions.liveauctioneers import (
            fetch_liveauctioneers_sitemap_entries,
        )

        kwargs = {
            "concurrency": concurrency,
            "max_sitemaps": max_sitemaps,
            "min_urls": min_urls,
            "sitemap_progress_path": sitemap_progress_path,
            "sitemap_force": sitemap_force,
        }
        if proxies:
            kwargs["proxies"] = proxies
        elif proxy is not None:
            kwargs["proxy"] = proxy
            kwargs["proxies"] = [proxy]
        return fetch_liveauctioneers_sitemap_entries(spec.default_indexes[0], **kwargs)

    if spec.name == "artcurial":
        from html_downloader.auctions.artcurial import fetch_artcurial_entries

        kwargs = {
            "concurrency": concurrency,
            "max_sales": max_sales,
            "sales_progress_path": sales_progress_path,
        }
        if proxies:
            kwargs["proxies"] = proxies
        elif proxy is not None:
            kwargs["proxy"] = proxy
            kwargs["proxies"] = [proxy]
        return fetch_artcurial_entries(spec.default_indexes[0], **kwargs)

    if spec.name == "barnebys":
        from html_downloader.auctions.barnebys import fetch_barnebys_sitemap_entries
        from html_downloader.auctions.barnebys_algolia import expand_lots_from_algolia

        kwargs = {
            "concurrency": concurrency,
            "max_sitemaps": max_sitemaps,
            "min_urls": min_urls,
            "sitemap_progress_path": sitemap_progress_path,
            "sitemap_force": sitemap_force,
            "include_realized": True,
        }
        if proxies:
            kwargs["proxies"] = proxies
        elif proxy is not None:
            kwargs["proxy"] = proxy
            kwargs["proxies"] = [proxy]
        entries = fetch_barnebys_sitemap_entries(spec.default_indexes[0], **kwargs)

        if expand_algolia:
            if algolia_state_path is None:
                raise ValueError("expand_algolia requires algolia_state_path for barnebys")
            try:
                search_entries = expand_lots_from_algolia(
                    state_path=algolia_state_path,
                    from_year=(
                        algolia_from_year
                        if algolia_from_year is not None
                        else 2000
                    ),
                    to_year=algolia_to_year,
                    workers=algolia_workers if algolia_workers > 0 else 1,
                    delay=algolia_delay if algolia_delay > 0 else 0.5,
                    force=algolia_force or sitemap_force,
                    supercategories=algolia_supercategories,
                    proxies=kwargs.get("proxies"),
                    art_only=True,
                )
                # Prefer search hits in the in-memory merge list; full archive
                # is streamed from JSONL in service.py.
                if search_entries:
                    by_key = {e.entity_key: e for e in entries if e.entity_id}
                    for entry in search_entries:
                        if entry.entity_id and entry.entity_key not in by_key:
                            by_key[entry.entity_key] = entry
                    entries = list(by_key.values())
            except Exception as exc:
                LOGGER.warning("barnebys search expansion skipped error=%s", exc)

        return entries

    if spec.name == "saleroom":
        from html_downloader.auctions.saleroom import fetch_saleroom_sitemap_entries

        if expand_algolia and algolia_state_path is None:
            raise ValueError("expand_algolia requires algolia_state_path for saleroom")
        kwargs = {
            "concurrency": concurrency,
            "expand_algolia": expand_algolia,
            "algolia_state_path": algolia_state_path,
            "algolia_delay": algolia_delay,
            "algolia_force": algolia_force or sitemap_force,
        }
        if proxies:
            kwargs["proxies"] = proxies
        elif proxy is not None:
            kwargs["proxy"] = proxy
            kwargs["proxies"] = [proxy]
        return fetch_saleroom_sitemap_entries(spec.default_indexes[0], **kwargs)

    if spec.name == "drouot":
        from html_downloader.auctions.drouot import fetch_drouot_sitemap_entries

        sale_index = (
            spec.default_indexes[1]
            if len(spec.default_indexes) > 1
            else DEFAULT_DROUOT_SALE_INDEX
        )
        if expand_algolia and algolia_state_path is None:
            raise ValueError("expand_algolia requires algolia_state_path for drouot")
        kwargs = {
            "concurrency": concurrency,
            "sale_index_url": sale_index,
            "max_sitemaps": max_sitemaps,
            "min_urls": min_urls,
            "sitemap_progress_path": sitemap_progress_path,
            "sitemap_force": sitemap_force,
            "expand_algolia": expand_algolia,
            "algolia_state_path": algolia_state_path,
            "algolia_delay": algolia_delay,
            "algolia_force": algolia_force or sitemap_force,
        }
        if proxies:
            kwargs["proxies"] = proxies
        elif proxy is not None:
            kwargs["proxy"] = proxy
            kwargs["proxies"] = [proxy]
        return fetch_drouot_sitemap_entries(spec.default_indexes[0], **kwargs)

    if spec.name == "christies":
        from html_downloader.auctions.christies import fetch_christies_entries

        art_mode = bool(algolia_supercategories)
        if art_mode and algolia_state_path is None:
            raise ValueError("christies art-only discovery requires algolia_state_path")
        return fetch_christies_entries(
            spec.default_indexes[0],
            concurrency=concurrency,
            proxies=proxies or ([proxy] if proxy is not None else None),
            max_sitemaps=max_sitemaps,
            min_urls=min_urls,
            sitemap_progress_path=sitemap_progress_path,
            sitemap_force=sitemap_force,
            art_categories=algolia_supercategories if art_mode else None,
            art_state_path=algolia_state_path,
            art_workers=algolia_workers if algolia_workers > 0 else 1,
            art_delay=algolia_delay if algolia_delay > 0 else 0.4,
            art_force=algolia_force,
            max_sales=max_sales,
        )

    raise ValueError(f"no discovery implementation for auction house {spec.name!r}")

"""Marketplace registry: indexes, concurrency, and sitemap fetch dispatch."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from html_downloader.discover.firstdibs import DEFAULT_1STDIBS_SEEDS, fetch_firstdibs_sitemap_entries
from html_downloader.discover.sitemap import (
    DEFAULT_ARTFINDER_INDEX,
    DEFAULT_ARTSY_INDEXES,
    DEFAULT_ARTMAJEUR_INDEX,
    DEFAULT_INDEX,
    DEFAULT_SAATCHI_INDEX,
    DEFAULT_SINGULART_INDEX,
    SitemapEntry,
    fetch_artfinder_sitemap_entries,
    fetch_artmajeur_sitemap_entries,
    fetch_artsper_sitemap_entries,
    fetch_artsy_sitemap_entries,
    fetch_saatchi_sitemap_entries,
    fetch_singulart_sitemap_entries,
)
from html_downloader.paths import MARKETPLACES


@dataclass(frozen=True, slots=True)
class MarketplaceSpec:
    name: str
    default_indexes: tuple[str, ...]
    default_concurrency: int
    uses_stealth_proxy: bool


SPECS: dict[str, MarketplaceSpec] = {
    "saatchi": MarketplaceSpec(
        name="saatchi",
        default_indexes=(DEFAULT_SAATCHI_INDEX,),
        default_concurrency=3,
        uses_stealth_proxy=False,
    ),
    "artsper": MarketplaceSpec(
        name="artsper",
        default_indexes=(DEFAULT_INDEX,),
        default_concurrency=8,
        uses_stealth_proxy=False,
    ),
    "artsy": MarketplaceSpec(
        name="artsy",
        default_indexes=tuple(DEFAULT_ARTSY_INDEXES),
        default_concurrency=8,
        uses_stealth_proxy=True,
    ),
    "artmajeur": MarketplaceSpec(
        name="artmajeur",
        default_indexes=(DEFAULT_ARTMAJEUR_INDEX,),
        default_concurrency=8,
        uses_stealth_proxy=True,
    ),
    "singulart": MarketplaceSpec(
        name="singulart",
        default_indexes=(DEFAULT_SINGULART_INDEX,),
        default_concurrency=8,
        uses_stealth_proxy=False,
    ),
    "firstdibs": MarketplaceSpec(
        name="firstdibs",
        default_indexes=DEFAULT_1STDIBS_SEEDS,
        default_concurrency=10,
        uses_stealth_proxy=False,
    ),
    "artfinder": MarketplaceSpec(
        name="artfinder",
        default_indexes=(DEFAULT_ARTFINDER_INDEX,),
        default_concurrency=4,
        uses_stealth_proxy=False,
    ),
}


def get_marketplace(name: str) -> MarketplaceSpec:
    if name not in SPECS:
        allowed = ", ".join(MARKETPLACES)
        raise ValueError(f"unknown marketplace {name!r}; expected one of: {allowed}")
    return SPECS[name]


def fetch_entries(
    spec: MarketplaceSpec,
    *,
    concurrency: int,
    proxy: dict[str, str] | None = None,
    indexes: Sequence[str] | None = None,
) -> list[SitemapEntry]:
    index_list = tuple(indexes) if indexes else spec.default_indexes
    if spec.name == "saatchi":
        return fetch_saatchi_sitemap_entries(index_list[0], concurrency=concurrency)
    if spec.name == "artsper":
        return fetch_artsper_sitemap_entries(index_list[0], concurrency=concurrency)
    if spec.name == "artmajeur":
        kwargs: dict[str, Any] = {"concurrency": concurrency}
        if proxy is not None:
            kwargs["proxy"] = proxy
        return fetch_artmajeur_sitemap_entries(index_list[0], **kwargs)
    if spec.name == "singulart":
        kwargs = {"concurrency": concurrency}
        if proxy is not None:
            kwargs["proxy"] = proxy
        return fetch_singulart_sitemap_entries(index_list[0], **kwargs)
    if spec.name == "firstdibs":
        kwargs = {"concurrency": concurrency}
        if proxy is not None:
            kwargs["proxy"] = proxy
        return fetch_firstdibs_sitemap_entries(index_list, **kwargs)
    if spec.name == "artfinder":
        return fetch_artfinder_sitemap_entries(index_list[0], concurrency=concurrency)
    kwargs = {"concurrency": concurrency}
    if proxy is not None:
        kwargs["proxy"] = proxy
    return fetch_artsy_sitemap_entries(index_list, **kwargs)

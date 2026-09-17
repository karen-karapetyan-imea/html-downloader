"""The Saleroom discovery via Algolia InstantSearch (lots_sr_en).

PRIMARY: filtered Algolia ``/query`` over art/collectables masterCategoryCodes
(FIA, DEA, AA, ETA, COL, GRE). Lot sitemaps and HTML ``/for-sale/`` browse are
no longer used (sitemaps return HTTP 405 from many clients; browse is AWS WAF).

Download of lot HTML still requires residential proxies (AWS WAF).
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

from html_downloader.auctions.saleroom_algolia import (
    DEFAULT_ALGOLIA_DELAY,
    expand_lots_from_algolia,
)
from html_downloader.auctions.urls import saleroom_entity_from_url
from html_downloader.discover.registry import load_urls
from html_downloader.discover.sitemap import SitemapEntry

LOGGER = logging.getLogger(__name__)

# Documented placeholder — discovery is Algolia-only (no XML sitemap fetch).
DEFAULT_SALEROOM_LOTS_INDEX = "algolia:lots_sr_en"

AWS_WAF_KEYWORDS: tuple[str, ...] = (
    "human verification",
    "captcha-container",
    "awswaf",
    "challenge.js",
    "captcha.js",
    "aws waf",
    "gokuprops",
)


def fetch_saleroom_sitemap_entries(
    sitemap_url: str = DEFAULT_SALEROOM_LOTS_INDEX,
    *,
    concurrency: int = 1,
    proxy: dict[str, str] | None = None,
    proxies: list[dict[str, str]] | None = None,
    expand_algolia: bool = True,
    algolia_state_path: Path | None = None,
    algolia_delay: float = DEFAULT_ALGOLIA_DELAY,
    algolia_force: bool = False,
) -> list[SitemapEntry]:
    """
    Discover Saleroom art/collectables lot URLs via Algolia.

    Returns a capped list of **new** entries from this run. Full coverage lives
    in the Algolia JSONL cache and is streamed into the job by service.py.
    """
    _ = (sitemap_url, concurrency, proxy, proxies)  # API parity with other houses
    if not expand_algolia:
        LOGGER.info("saleroom discover skipped (expand_algolia=False)")
        return []
    if algolia_state_path is None:
        raise ValueError("saleroom Algolia discovery requires algolia_state_path")

    entries = expand_lots_from_algolia(
        state_path=algolia_state_path,
        delay=algolia_delay if algolia_delay > 0 else DEFAULT_ALGOLIA_DELAY,
        force=algolia_force,
    )
    LOGGER.info(
        "saleroom algolia discover new_in_memory=%s "
        "(full set streams from lot JSONL cache at job write)",
        len(entries),
    )
    return entries


def known_saleroom_keys_from_paths(paths: Iterable[Path]) -> set[tuple[str, str]]:
    """Load known Saleroom entity keys from URL list / JSONL files."""
    keys: set[tuple[str, str]] = set()
    for url in load_urls(paths):
        key = saleroom_entity_from_url(url)
        if key is not None:
            keys.add(key)
    return keys


def save_auction_lastmod_state(path: Path, entities: dict[str, str]) -> None:
    """Atomic write of auction lastmod state (temp file + replace)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "entities": dict(sorted(entities.items())),
        "last_fetch_at": datetime.now().astimezone().isoformat(),
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


__all__ = [
    "AWS_WAF_KEYWORDS",
    "DEFAULT_SALEROOM_LOTS_INDEX",
    "fetch_saleroom_sitemap_entries",
    "known_saleroom_keys_from_paths",
    "save_auction_lastmod_state",
]

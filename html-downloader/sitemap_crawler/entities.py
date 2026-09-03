"""Entity URL classification for 1stDibs art sitemap crawler."""

from __future__ import annotations

import re
from typing import Literal
from urllib.parse import urlsplit

EntityKind = Literal["item", "dealer", "creator"]

_LOCALE_PATH_PREFIXES = ("/fr/", "/de/", "/it/", "/es/")
_CREATOR_LOCALE_SEGMENTS = ("/creators/fr/", "/creators/de/", "/creators/it/", "/creators/es/")
_CREATOR_HUB_SLUGS = frozenset({"jewelry", "fashion", "furniture"})

ARTWORK_RE = re.compile(
    r"^https://www\.1stdibs\.com/art/[^?#]*/id-a_(\d+)/?$",
    re.IGNORECASE,
)
DEALER_RE = re.compile(
    r"^https://www\.1stdibs\.com/dealers/([a-z0-9_-]+)/?$",
    re.IGNORECASE,
)
CREATOR_RE = re.compile(
    r"^https://www\.1stdibs\.com/creators/([a-z0-9_-]+)(?:/(.*))?$",
    re.IGNORECASE,
)


def is_english_url(url: str) -> bool:
    """Return True when URL path is English (no locale prefix)."""
    path = urlsplit(url).path.lower()
    if any(path.startswith(prefix) for prefix in _LOCALE_PATH_PREFIXES):
        return False
    return not any(segment in path for segment in _CREATOR_LOCALE_SEGMENTS)


def classify_url(url: str) -> tuple[EntityKind, str, str] | None:
    """Classify a normalized URL into an entity type, dedup key, and canonical URL."""
    if not url or "?" in url or "#" in url:
        return None
    if not is_english_url(url):
        return None
    lower = url.lower()
    if "/sitemap/" in lower or "/search/" in lower:
        return None

    artwork = ARTWORK_RE.match(url)
    if artwork:
        item_id = artwork.group(1)
        return "item", item_id, url

    dealer = DEALER_RE.match(url)
    if dealer:
        slug = dealer.group(1).lower()
        return "dealer", slug, url

    creator = CREATOR_RE.match(url)
    if creator:
        slug = creator.group(1).lower()
        if slug in _CREATOR_HUB_SLUGS:
            return None
        remainder = (creator.group(2) or "").strip("/")
        if remainder in _CREATOR_HUB_SLUGS:
            return None
        if remainder.startswith("art"):
            canonical = f"https://www.1stdibs.com/creators/{slug}/art/"
        else:
            canonical = f"https://www.1stdibs.com/creators/{slug}/"
        return "creator", slug, canonical

    return None


def merge_entity_url(
    existing: str | None,
    new_url: str,
    *,
    kind: EntityKind,
) -> str:
    """Pick the best canonical URL when the same entity is seen multiple times."""
    if existing is None:
        return new_url
    if kind != "creator":
        return existing
    if existing.endswith("/art/"):
        return existing
    if new_url.endswith("/art/"):
        return new_url
    return existing

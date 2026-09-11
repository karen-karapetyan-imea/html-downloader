"""URL normalization and auction-page classification for auction crawlers."""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, unquote, urlencode, urljoin, urlsplit, urlunsplit

_TRACKING_PARAMS = frozenset(
    {
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_term",
        "utm_content",
        "utm_id",
        "fbclid",
        "gclid",
        "mc_cid",
        "mc_eid",
        "msclkid",
        "_ga",
        "ref",
        "referrer",
        "objectid",
        "algindex",
        "queryid",
    }
)

# Auction event / catalog page: /catalog/{id} (not advancedSearch.cfm, etc.)
INVALUABLE_CATALOG_RE = re.compile(
    r"^https://www\.invaluable\.com/catalog/([A-Za-z0-9]{6,})/?$",
    re.IGNORECASE,
)

# Individual lot page: /auction-lot/{slug}-c-{hexId}
INVALUABLE_LOT_RE = re.compile(
    r"^https://www\.invaluable\.com/auction-lot/(.+)-c-([A-Fa-f0-9]{6,})/?$",
    re.IGNORECASE,
)

# Short lot form used by some clients: /lot/{lotRef}
INVALUABLE_LOT_SHORT_RE = re.compile(
    r"^https://www\.invaluable\.com/lot/([A-Za-z0-9]{6,})/?$",
    re.IGNORECASE,
)

# /auction-house/{slug}-{id}
INVALUABLE_HOUSE_RE = re.compile(
    r"^https://www\.invaluable\.com/auction-house/(.+)-([A-Za-z0-9]{6,})/?$",
    re.IGNORECASE,
)

# Alternate: /auction-houses/{id}
INVALUABLE_HOUSE_ALT_RE = re.compile(
    r"^https://www\.invaluable\.com/auction-houses/([A-Za-z0-9]{6,})/?$",
    re.IGNORECASE,
)

# Artist profile only (not sold-at-auction / lots-at-auction seeds)
INVALUABLE_ARTIST_RE = re.compile(
    r"^https://www\.invaluable\.com/artist/([A-Za-z0-9-]+)-([A-Za-z0-9]{6,})/?$",
    re.IGNORECASE,
)

# Category tree: /{slug}/pc|cc|sc-{ID}/
INVALUABLE_CATEGORY_RE = re.compile(
    r"^https://www\.invaluable\.com/([A-Za-z0-9-]+)/(pc|cc|sc)-([A-Za-z0-9]{6,})/?$",
    re.IGNORECASE,
)

_ARTIST_SEED_SUFFIXES = (
    "/sold-at-auction-prices",
    "/lots-at-auction",
)

_INVALUABLE_HOSTS = frozenset({"www.invaluable.com", "invaluable.com"})
_LIVEAUCTIONEERS_HOSTS = frozenset({"www.liveauctioneers.com", "liveauctioneers.com"})

# SEO sold-lot page: /price-result/{slug}/
LIVEAUCTIONEERS_PRICE_RESULT_RE = re.compile(
    r"^https://www\.liveauctioneers\.com/price-result/([^/\"'<>\s]+)/?$",
    re.IGNORECASE,
)


def normalize_auction_url(url: str, *, base_url: str | None = None) -> str | None:
    """
    Normalize an auction-related URL.

    - Resolve relative URLs when base_url is provided
    - Require http(s)
    - Drop fragments
    - Strip common tracking / Algolia query params
    - Lowercase scheme/host; force www.invaluable.com / www.liveauctioneers.com
    - Catalog / lot / house / artist / category / price-result ids lowercased
    - Strip trailing slash (except bare `/`)
    """
    text = (url or "").strip()
    if not text or text.startswith(("#", "javascript:", "mailto:")):
        return None

    if base_url:
        text = urljoin(base_url, text)

    parts = urlsplit(text)
    scheme = (parts.scheme or "").lower()
    if scheme not in {"http", "https"}:
        return None

    host = (parts.hostname or "").lower()
    if not host:
        return None

    if host == "invaluable.com":
        host = "www.invaluable.com"
        netloc = host
    elif host == "liveauctioneers.com":
        host = "www.liveauctioneers.com"
        netloc = host
    else:
        netloc = host
        if parts.port and parts.port not in (80, 443):
            netloc = f"{host}:{parts.port}"

    path = unquote(parts.path or "/")

    catalog_match = re.match(r"^(/catalog/)([A-Za-z0-9]{6,})(/?)$", path, re.IGNORECASE)
    lot_match = re.match(
        r"^(/auction-lot/)(.+)-c-([A-Fa-f0-9]{6,})(/?)$",
        path,
        re.IGNORECASE,
    )
    lot_short = re.match(r"^(/lot/)([A-Za-z0-9]{6,})(/?)$", path, re.IGNORECASE)
    house_match = re.match(
        r"^(/auction-house/)(.+)-([A-Za-z0-9]{6,})(/?)$",
        path,
        re.IGNORECASE,
    )
    house_alt = re.match(r"^(/auction-houses/)([A-Za-z0-9]{6,})(/?)$", path, re.IGNORECASE)
    artist_match = re.match(
        r"^(/artist/)([A-Za-z0-9-]+)-([A-Za-z0-9]{6,})(/.*)?$",
        path,
        re.IGNORECASE,
    )
    category_match = re.match(
        r"^(/[A-Za-z0-9-]+)/(pc|cc|sc)-([A-Za-z0-9]{6,})(/?)$",
        path,
        re.IGNORECASE,
    )
    price_result_match = re.match(
        r"^(/price-result/)([^/\"'<>\s]+)(/?)$",
        path,
        re.IGNORECASE,
    )

    if catalog_match and host in _INVALUABLE_HOSTS:
        path = f"/catalog/{catalog_match.group(2).lower()}"
    elif lot_match and host in _INVALUABLE_HOSTS:
        slug = lot_match.group(2)
        lot_id = lot_match.group(3).lower()
        path = f"/auction-lot/{slug}-c-{lot_id}"
    elif lot_short and host in _INVALUABLE_HOSTS:
        path = f"/lot/{lot_short.group(2).lower()}"
    elif house_match and host in _INVALUABLE_HOSTS:
        path = f"/auction-house/{house_match.group(2).lower()}-{house_match.group(3).lower()}"
    elif house_alt and host in _INVALUABLE_HOSTS:
        path = f"/auction-houses/{house_alt.group(2).lower()}"
    elif artist_match and host in _INVALUABLE_HOSTS:
        slug = artist_match.group(2).lower()
        artist_id = artist_match.group(3).lower()
        rest = (artist_match.group(4) or "").rstrip("/")
        # Keep sold / lots-at-auction paths for seed normalization callers
        if rest:
            path = f"/artist/{slug}-{artist_id}{rest.lower()}"
        else:
            path = f"/artist/{slug}-{artist_id}"
    elif category_match and host in _INVALUABLE_HOSTS:
        slug = category_match.group(1).lstrip("/").lower()
        kind = category_match.group(2).lower()
        cat_id = category_match.group(3).lower()
        path = f"/{slug}/{kind}-{cat_id}"
    elif price_result_match and host in _LIVEAUCTIONEERS_HOSTS:
        path = f"/price-result/{price_result_match.group(2).lower()}"
    else:
        path = path.rstrip("/") or "/"

    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")

    filtered_query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key.lower() not in _TRACKING_PARAMS
    ]
    query = urlencode(filtered_query, doseq=True)

    # Entity crawl targets drop query entirely
    if host in _INVALUABLE_HOSTS:
        candidate = urlunsplit(("https", "www.invaluable.com", path, "", ""))
        if invaluable_entity_from_url_path(candidate) is not None:
            query = ""
        out_netloc = "www.invaluable.com"
    elif host in _LIVEAUCTIONEERS_HOSTS:
        candidate = urlunsplit(("https", "www.liveauctioneers.com", path, "", ""))
        if liveauctioneers_entity_from_url_path(candidate) is not None:
            query = ""
        out_netloc = "www.liveauctioneers.com"
    else:
        out_netloc = netloc

    return urlunsplit(("https", out_netloc, path, query, ""))


def _is_artist_seed_path(path: str) -> bool:
    lower = path.lower().rstrip("/")
    return any(lower.endswith(suffix) for suffix in _ARTIST_SEED_SUFFIXES)


def invaluable_entity_from_url_path(url: str) -> tuple[str, str] | None:
    """Classify a normalized absolute URL without re-entering normalize."""
    lot = INVALUABLE_LOT_RE.match(url)
    if lot:
        return "lot", lot.group(2).lower()

    lot_short = INVALUABLE_LOT_SHORT_RE.match(url)
    if lot_short:
        return "lot", lot_short.group(1).lower()

    catalog = INVALUABLE_CATALOG_RE.match(url)
    if catalog:
        return "catalog", catalog.group(1).lower()

    house = INVALUABLE_HOUSE_RE.match(url)
    if house:
        return "house", house.group(2).lower()

    house_alt = INVALUABLE_HOUSE_ALT_RE.match(url)
    if house_alt:
        return "house", house_alt.group(1).lower()

    # Artist profiles only — sold-at-auction pages are discovery seeds
    path = urlsplit(url).path or ""
    if _is_artist_seed_path(path):
        return None
    artist = INVALUABLE_ARTIST_RE.match(url)
    if artist:
        return "artist", artist.group(2).lower()

    category = INVALUABLE_CATEGORY_RE.match(url)
    if category:
        kind = category.group(2).lower()
        cat_id = category.group(3).lower()
        return "category", f"{kind}-{cat_id}"

    return None


def is_auction_url(url: str) -> bool:
    """True for Invaluable crawl targets (lot/catalog/house/artist/category)."""
    return invaluable_entity_from_url(url) is not None


def invaluable_entity_from_url(url: str) -> tuple[str, str] | None:
    """
    Return (entity_type, entity_id) for Invaluable crawl targets.

    Accepted:
      /auction-lot/{slug}-c-{hexId}  → ("lot", hexId)
      /lot/{lotRef}                  → ("lot", lotRef)
      /catalog/{id}                  → ("catalog", id)
      /auction-house/{slug}-{id}     → ("house", id)
      /auction-houses/{id}           → ("house", id)
      /artist/{slug}-{id}            → ("artist", id)  (profile only)
      /{slug}/pc|cc|sc-{ID}          → ("category", "{kind}-{id}")

    Rejected: sold-at-auction seeds, search, directory indexes, etc.
    """
    normalized = normalize_auction_url(url)
    if not normalized:
        return None
    host = (urlsplit(normalized).hostname or "").lower()
    if host not in _INVALUABLE_HOSTS:
        return None
    return invaluable_entity_from_url_path(normalized)


def prefer_entity_url(existing: str, candidate: str) -> str:
    """Pick the more canonical URL when two URLs share an entity key."""
    ex = invaluable_entity_from_url(existing)
    cand = invaluable_entity_from_url(candidate)
    if ex is None:
        return candidate
    if cand is None:
        return existing
    if ex != cand:
        return existing

    def score(url: str) -> int:
        if "/auction-lot/" in url:
            return 3
        if "/auction-house/" in url:
            return 3
        if "/lot/" in url:
            return 1
        if "/auction-houses/" in url:
            return 1
        return 2

    return candidate if score(candidate) > score(existing) else existing


def is_invaluable_auction_url(url: str) -> bool:
    """Alias for is_auction_url scoped to Invaluable entity pages."""
    return is_auction_url(url)


def liveauctioneers_entity_from_url_path(url: str) -> tuple[str, str] | None:
    """Classify a normalized LiveAuctioneers absolute URL without re-entering normalize."""
    match = LIVEAUCTIONEERS_PRICE_RESULT_RE.match(url)
    if match:
        return "price_result", match.group(1).lower()
    return None


def liveauctioneers_entity_from_url(url: str) -> tuple[str, str] | None:
    """
    Return (entity_type, entity_id) for LiveAuctioneers crawl targets.

    Accepted:
      /price-result/{slug}  → ("price_result", slug)
    """
    normalized = normalize_auction_url(url)
    if not normalized:
        return None
    host = (urlsplit(normalized).hostname or "").lower()
    if host not in _LIVEAUCTIONEERS_HOSTS:
        return None
    return liveauctioneers_entity_from_url_path(normalized)


def is_liveauctioneers_auction_url(url: str) -> bool:
    """True for LiveAuctioneers price-result crawl targets."""
    return liveauctioneers_entity_from_url(url) is not None

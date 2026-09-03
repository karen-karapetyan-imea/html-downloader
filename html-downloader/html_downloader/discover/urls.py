"""URL parsing helpers for Artsper, Saatchi, Artsy, ArtMajeur, Singulart, Artfinder, Fine Art America, Phaidon, and 1stDibs."""

from __future__ import annotations

import re

ARTSPER_ARTWORK_RE = re.compile(
    r"artsper\.com/(?:[a-z]{2}/)?contemporary-artworks/[^/]+/(\d+)/",
    re.IGNORECASE,
)
ARTSPER_ARTIST_RE = re.compile(
    r"artsper\.com/(?:[a-z]{2}/)?contemporary-artists/[^/]+/(\d+)/",
    re.IGNORECASE,
)
SAATCHI_ARTWORK_RE = re.compile(
    r"saatchiart\.com/art/[^/]+/(\d+)/(\d+)(?:/view)?/?",
    re.IGNORECASE,
)
SAATCHI_ARTIST_PROFILE_RE = re.compile(
    r"saatchiart\.com/account/profile/([a-z0-9_-]+)/?$",
    re.IGNORECASE,
)
SAATCHI_ARTIST_ID_RE = re.compile(
    r"saatchiart\.com/(?:account/profile/)?(\d+)/?$",
    re.IGNORECASE,
)
SAATCHI_USERNAME_RE = re.compile(
    r"saatchiart\.com/([a-z0-9_-]+)/?$",
    re.IGNORECASE,
)

_SAATCHI_RESERVED = frozenset(
    {
        "art",
        "account",
        "accounts",
        "search",
        "collections",
        "stories",
        "magazine",
        "api",
        "www",
    }
)
ARTSY_ARTWORK_RE = re.compile(
    r"artsy\.net/artwork/([a-z0-9-]+)/?(?:[?#].*)?$",
    re.IGNORECASE,
)
ARTSY_ARTIST_RE = re.compile(
    r"artsy\.net/artist/([a-z0-9-]+)/?(?:[?#].*)?$",
    re.IGNORECASE,
)
ARTMAJEUR_ARTWORK_RE = re.compile(
    r"artmajeur\.com/[^/]+/(?:[a-z]{2}/)?artworks/(\d+)/",
    re.IGNORECASE,
)
ARTMAJEUR_ARTIST_RE = re.compile(
    r"artmajeur\.com/([a-z0-9_-]+)/?$",
    re.IGNORECASE,
)

SINGULART_ARTIST_RE = re.compile(
    r"singulart\.com/(?:[a-z]{2}/)?artist/[^/?#]+-(\d+)/?$",
    re.IGNORECASE,
)
SINGULART_ARTWORK_RE = re.compile(
    r"singulart\.com/(?:[a-z]{2}/)artworks/[^/?#]+-(\d+)/?$",
    re.IGNORECASE,
)
FIRSTDIBS_ITEM_RE = re.compile(
    r"1stdibs\.com/art/[^?#]*/id-a_(\d+)/?",
    re.IGNORECASE,
)
FIRSTDIBS_DEALER_RE = re.compile(
    r"1stdibs\.com/dealers/([a-z0-9_-]+)",
    re.IGNORECASE,
)
ARTFINDER_ARTWORK_RE = re.compile(
    r"artfinder\.com/product/([a-z0-9_-]+)/?$",
    re.IGNORECASE,
)
ARTFINDER_ARTIST_RE = re.compile(
    r"artfinder\.com/artist/([a-z0-9_-]+)/?$",
    re.IGNORECASE,
)
FINEARTAMERICA_ARTIST_RE = re.compile(
    r"fineartamerica\.com/profiles/([a-z0-9_-]+)/?$",
    re.IGNORECASE,
)
FINEARTAMERICA_ARTWORK_RE = re.compile(
    r"fineartamerica\.com/featured/([a-z0-9_-]+)\.html/?$",
    re.IGNORECASE,
)
PHAIDON_PRODUCT_RE = re.compile(
    r"phaidon\.com/products/([a-z0-9-]+)/?$",
    re.IGNORECASE,
)

_ARTMAJEUR_RESERVED = frozenset(
    {
        "en",
        "fr",
        "de",
        "es",
        "it",
        "pt",
        "nl",
        "pl",
        "ru",
        "ja",
        "zh",
        "magazine",
        "help",
        "search",
        "blog",
        "faq",
        "www",
        "api",
        "cdn-cgi",
    }
)


def artsper_entity_from_url(url: str) -> tuple[str, str] | None:
    """Return ('artwork'|'artist', numeric_id) for Artsper entity URLs."""
    artwork = ARTSPER_ARTWORK_RE.search(url)
    if artwork:
        return "artwork", artwork.group(1)
    artist = ARTSPER_ARTIST_RE.search(url)
    if artist:
        return "artist", artist.group(1)
    return None


def saatchi_artwork_from_url(url: str) -> tuple[str, str] | None:
    """Return (artist_id, artwork_id) from Saatchi artwork URLs."""
    match = SAATCHI_ARTWORK_RE.search(url)
    if not match:
        return None
    return match.group(1), match.group(2)


def saatchi_artist_from_url(url: str) -> str | None:
    """Return numeric artist id when present in Saatchi profile URLs."""
    match = SAATCHI_ARTIST_ID_RE.search(url)
    if match:
        return match.group(1)
    profile = SAATCHI_ARTIST_PROFILE_RE.search(url)
    if profile and profile.group(1).isdigit():
        return profile.group(1)
    return None


def saatchi_entity_from_url(url: str) -> tuple[str, str] | None:
    """Return ('artwork'|'artist', external_id) for Saatchi entity URLs."""
    artwork = saatchi_artwork_from_url(url)
    if artwork:
        return "artwork", artwork[1]
    artist_id = saatchi_artist_from_url(url)
    if artist_id:
        return "artist", artist_id
    profile = SAATCHI_ARTIST_PROFILE_RE.search(url)
    if profile:
        return "artist", profile.group(1)
    username = SAATCHI_USERNAME_RE.search(url)
    if username:
        slug = username.group(1).lower()
        if slug not in _SAATCHI_RESERVED:
            return "artist", slug
    return None


def artsy_entity_from_url(url: str) -> tuple[str, str] | None:
    """Return ('artwork'|'artist', slug) for Artsy entity page URLs."""
    artwork = ARTSY_ARTWORK_RE.search(url)
    if artwork:
        return "artwork", artwork.group(1).lower()
    artist = ARTSY_ARTIST_RE.search(url)
    if artist:
        return "artist", artist.group(1).lower()
    return None


def artmajeur_entity_from_url(url: str) -> tuple[str, str] | None:
    """Return ('artwork'|'artist', external_id) for ArtMajeur entity page URLs."""
    artwork = ARTMAJEUR_ARTWORK_RE.search(url)
    if artwork:
        return "artwork", artwork.group(1)
    artist = ARTMAJEUR_ARTIST_RE.search(url)
    if artist:
        slug = artist.group(1).lower()
        if slug not in _ARTMAJEUR_RESERVED:
            return "artist", slug
    return None


def singulart_entity_from_url(url: str) -> tuple[str, str] | None:
    """Return ('artwork'|'artist', numeric_id) for Singulart entity page URLs."""
    if "?" in url or "#" in url:
        return None
    artist = SINGULART_ARTIST_RE.search(url)
    if artist:
        return "artist", artist.group(1)
    artwork = SINGULART_ARTWORK_RE.search(url)
    if artwork:
        return "artwork", artwork.group(1)
    return None


def firstdibs_entity_from_url(url: str) -> tuple[str, str] | None:
    """Return ('item'|'dealer'|'creator', external_id) for 1stDibs entity page URLs."""
    if "?" in url or "#" in url:
        return None
    lower = url.lower()
    if "/sitemap/" in lower or "/search/" in lower or "/item/" in lower:
        return None

    from sitemap_crawler.entities import classify_url

    classified = classify_url(url)
    if classified is not None:
        kind, key, _canonical = classified
        return kind, key

    item = FIRSTDIBS_ITEM_RE.search(url)
    if item:
        return "item", item.group(1)
    dealer = FIRSTDIBS_DEALER_RE.search(url)
    if dealer:
        return "dealer", dealer.group(1).lower()
    return None


def artfinder_entity_from_url(url: str) -> tuple[str, str] | None:
    """Return ('artwork'|'artist', slug) for Artfinder entity page URLs.

    Accepts only default-locale paths (/product/{slug}/, /artist/{slug}/).
    Rejects query/fragment and locale-prefixed paths such as /en-US/product/...
    """
    if "?" in url or "#" in url:
        return None
    artwork = ARTFINDER_ARTWORK_RE.search(url)
    if artwork:
        return "artwork", artwork.group(1).lower()
    artist = ARTFINDER_ARTIST_RE.search(url)
    if artist:
        return "artist", artist.group(1).lower()
    return None


def fineartamerica_entity_from_url(url: str) -> tuple[str, str] | None:
    """Return ('artwork'|'artist', slug) for Fine Art America entity page URLs.

    Artist: /profiles/{slug} (no nested /shop|/art/...).
    Artwork: /featured/{slug}.html
    Rejects query/fragment.
    """
    if "?" in url or "#" in url:
        return None
    artwork = FINEARTAMERICA_ARTWORK_RE.search(url)
    if artwork:
        return "artwork", artwork.group(1).lower()
    artist = FINEARTAMERICA_ARTIST_RE.search(url)
    if artist:
        return "artist", artist.group(1).lower()
    return None


def phaidon_entity_from_url(url: str) -> tuple[str, str] | None:
    """Return ('product', slug) for Phaidon product page URLs.

    Accepts only default-locale /products/{slug} paths.
    Rejects query/fragment and locale-prefixed paths such as /en-us/products/...
    """
    if "?" in url or "#" in url:
        return None
    product = PHAIDON_PRODUCT_RE.search(url)
    if product:
        return "product", product.group(1).lower()
    return None

"""URL parsing helpers for Artsper, Saatchi, Artsy, and ArtMajeur."""

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

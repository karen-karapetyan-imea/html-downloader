"""Value normalizers shared by all platform mappers."""

import html
import math
import re
from collections.abc import Iterable
from dataclasses import dataclass, fields
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from functools import cache
from typing import Any
from urllib.parse import urlsplit

import pycountry

_INVISIBLE = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u2060\ufeff\x00"), None)
_PLACEHOLDERS = frozenset(
    {"none", "null", "nan", "n/a", "na", "-", "--", "unknown", "unknown date", "undefined"}
)
_TAG_RE = re.compile(r"</?[a-zA-Z][^>]*>")
_BREAK_TAG_RE = re.compile(r"<\s*(?:br|/p|/div|/li|/h[1-6])\b[^>]*>", re.I)
_SCHEME_RE = re.compile(r"^[a-z][a-z0-9+.-]*://", re.I)
_BARE_SCHEME_RE = re.compile(r"^[a-z][a-z0-9+-]*:(?!\d)", re.I)  # mailto:, tel:, javascript: (not host:port)
_YEAR_RE = re.compile(r"\b(\d{4})\b")
_HANDLE_RE = re.compile(r"^[A-Za-z0-9_.\-]+$")
_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
MIN_YEAR = 1000
CENT = Decimal("0.01")
MAX_PRICE = Decimal("999999999999.99")  # fits the decimal128(14, 2) price columns

SOCIAL_DOMAINS: dict[str, tuple[str, ...]] = {
    "facebook": ("facebook.com", "fb.com", "fb.me"),
    "instagram": ("instagram.com", "instagr.am"),
    "tiktok": ("tiktok.com",),
    "twitter": ("twitter.com", "x.com"),
}
# Links that are never an artist's own website: other social networks and art marketplaces.
NON_WEBSITE_DOMAINS: tuple[str, ...] = (
    "pinterest.com",
    "youtube.com",
    "youtu.be",
    "linkedin.com",
    "tumblr.com",
    "vimeo.com",
    "flickr.com",
    "behance.net",
    "threads.net",
    "snapchat.com",
    "deviantart.com",
    "dribbble.com",
    "linktr.ee",
    "artmajeur.com",
    "artsper.com",
    "saatchiart.com",
    "artsy.net",
    "artfinder.com",
    "singulart.com",
    "etsy.com",
    "ebay.com",
    "amazon.com",
)
PROFILE_URL_TEMPLATES: dict[str, str] = {
    "facebook": "https://www.facebook.com/{}",
    "instagram": "https://www.instagram.com/{}",
    "tiktok": "https://www.tiktok.com/@{}",
    "twitter": "https://twitter.com/{}",
}
_COUNTRY_ALIASES = {"UK": "United Kingdom", "USA": "United States", "US": "United States"}
_GENDERS = {"male": "male", "man": "male", "m": "male", "female": "female", "woman": "female", "f": "female"}


def clean_text(value: object, *, multiline: bool = False) -> str | None:
    """Trim, drop invisible characters and placeholders; collapse whitespace.

    With multiline=True line breaks are kept (at most one blank line in a row).
    """
    if value is None:
        return None
    text = (value if isinstance(value, str) else str(value)).translate(_INVISIBLE).replace("\xa0", " ")
    if multiline:
        text = "\n".join(" ".join(line.split()) for line in text.splitlines())
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
    else:
        text = " ".join(text.split())
    if not text or text.lower() in _PLACEHOLDERS:
        return None
    return text


def platform_id(value: object) -> str | None:
    return clean_text(value)


def as_list(value: object) -> list[Any]:
    if value is None:
        return []
    return list(value) if isinstance(value, list | tuple) else [value]


def str_list(*values: object) -> list[str] | None:
    """Clean, de-duplicated (order kept) strings from scalars and/or lists; None when empty."""
    items = (clean_text(v) for value in values for v in as_list(value))
    return list(dict.fromkeys(i for i in items if i is not None)) or None


def to_float(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value if not isinstance(value, str) else value.replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def to_price(value: object) -> Decimal | None:
    """Non-negative amount rounded to cents; None for anything else (text, NaN, overflow)."""
    number = to_float(value)
    if number is None or number < 0:
        return None
    try:
        price = Decimal(str(number)).quantize(CENT, rounding=ROUND_HALF_UP)
    except InvalidOperation:
        return None
    return price if price <= MAX_PRICE else None


def currency_code(value: object) -> str | None:
    text = clean_text(value)
    if text is None:
        return None
    text = text.upper()
    return text if _CURRENCY_RE.fullmatch(text) else None


def to_bool(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def plain_text(value: object) -> str | None:
    """HTML or text -> plain text; block-level tags become line breaks."""
    if not isinstance(value, str):
        return clean_text(value, multiline=True)
    if _TAG_RE.search(value):
        value = _TAG_RE.sub("", _BREAK_TAG_RE.sub("\n", value))
    return clean_text(html.unescape(value), multiline=True)


def sane_year(value: object) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        year = value
    else:
        m = _YEAR_RE.search(str(value))
        if not m:
            return None
        year = int(m.group(1))
    return year if MIN_YEAR <= year <= date.today().year else None


def normalize_gender(value: object) -> str | None:
    text = clean_text(value)
    if text is None:
        return None
    key = text.rsplit("/", 1)[-1].lower()  # schema.org values look like "https://schema.org/Female"
    return _GENDERS.get(key, key)


@cache
def country_name(value: object) -> str | None:
    """ISO alpha-2/alpha-3 codes become country names; names pass through unchanged."""
    text = clean_text(value)
    if text is None:
        return None
    if text.upper() in _COUNTRY_ALIASES:
        return _COUNTRY_ALIASES[text.upper()]
    if text.isalpha() and (len(text) == 2 or (len(text) == 3 and text.isupper())):
        country = pycountry.countries.get(**{f"alpha_{len(text)}": text.upper()})
        if country is not None:
            return getattr(country, "common_name", None) or country.name
    return text


def to_url(value: object) -> str | None:
    """Absolute http(s) URL or None; scheme-less values like 'www.example.com' get https://."""
    text = clean_text(value)
    if text is None or " " in text:
        return None
    if text.startswith("//"):
        text = "https:" + text
    elif not _SCHEME_RE.match(text):
        if _BARE_SCHEME_RE.match(text) or "." not in text.split("/", 1)[0]:
            return None
        text = "https://" + text
    parts = urlsplit(text)
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        return None
    return text


def host_of(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    for prefix in ("www.", "m.", "mobile."):
        if host.startswith(prefix):
            return host[len(prefix) :]
    return host


def _on_domain(host: str, domains: Iterable[str]) -> bool:
    return any(host == d or host.endswith("." + d) for d in domains)


def social_kind(url: str) -> str | None:
    host = host_of(url)
    return next((kind for kind, domains in SOCIAL_DOMAINS.items() if _on_domain(host, domains)), None)


def username_to_url(platform: str, value: object) -> str | None:
    """A social handle ('@jane', 'jane') or URL -> profile URL for that platform."""
    text = clean_text(value)
    if text is None:
        return None
    is_url = "/" in text or _SCHEME_RE.match(text) is not None or social_kind("https://" + text) is not None
    if is_url:
        return to_url(text)
    handle = text.lstrip("@")
    if not _HANDLE_RE.match(handle):
        return None
    return PROFILE_URL_TEMPLATES[platform].format(handle)


@dataclass(frozen=True, slots=True)
class Links:
    website: str | None = None
    facebook: str | None = None
    instagram: str | None = None
    tiktok: str | None = None
    twitter: str | None = None

    def fill_from(self, other: "Links") -> "Links":
        """Keep own values, take the other's where ours are missing."""
        return Links(**{f.name: getattr(self, f.name) or getattr(other, f.name) for f in fields(self)})


def classify_social_links(urls: Iterable[object]) -> Links:
    """Sort a mixed list of links into social profiles; the first other link is the website."""
    found: dict[str, str] = {}
    for raw in urls:
        url = to_url(raw)
        if url is None:
            continue
        kind = social_kind(url)
        if kind is not None:
            found.setdefault(kind, url)
        elif not _on_domain(host_of(url), NON_WEBSITE_DOMAINS):
            found.setdefault("website", url)
    return Links(**found)

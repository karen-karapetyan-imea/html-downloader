"""Parse one UGallery page (raw HTML): artwork products (/products/<slug>) and artist
pages (/pages/<slug>). Prefer JSON-LD Product / Person; fall back to og meta and DOM.
"""

from __future__ import annotations

import re
from typing import Any

from bs4 import BeautifulSoup, Tag

from etl_core.jsonld import LdObject, first_of_type, jsonld_objects

ARTWORK_URL_RE = re.compile(r"ugallery\.com/products/([a-z0-9-]+)/?", re.I)
ARTIST_URL_RE = re.compile(r"ugallery\.com/pages/([a-z0-9-]+)/?", re.I)
DIM_RE = re.compile(
    r"""(?P<h>\d+(?:\.\d+)?)\s*"?\s*h\s*x\s*(?P<w>\d+(?:\.\d+)?)\s*"?\s*w""",
    re.I,
)
YEAR_RE = re.compile(r"\b((?:19|20)\d{2})\b")
PAGE_RESERVED = frozenset(
    {
        "contact",
        "terms",
        "about-us",
        "faq",
        "for-artists",
        "commissions",
        "quiz",
        "privacy-policy",
        "accessibility",
        "a-guide-to-choosing-art-for-your-home",
        "how-to-decorate-your-living-room-with-original-art",
    }
)


def slug(pattern: re.Pattern[str], url: object) -> str | None:
    m = pattern.search(url) if isinstance(url, str) else None
    return m.group(1).lower() if m else None


def meta_content(soup: BeautifulSoup, prop: str) -> str | None:
    tag = soup.find("meta", property=prop) or soup.find("meta", attrs={"name": prop})
    return str(tag["content"]) if isinstance(tag, Tag) and tag.get("content") else None


def first_image(value: object) -> str | None:
    if isinstance(value, list):
        value = value[0] if value else None
    if isinstance(value, dict):
        value = value.get("url") or value.get("contentUrl")
    return value if isinstance(value, str) else None


def absolute_url(url: str | None) -> str | None:
    if not url:
        return None
    if url.startswith("//"):
        return f"https:{url}"
    return url


def page_url(soup: BeautifulSoup) -> str | None:
    if og := meta_content(soup, "og:url"):
        return og
    tag = soup.find("link", rel="canonical")
    return str(tag["href"]) if isinstance(tag, Tag) and tag.get("href") else None


def parse_page(html: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")
    url = page_url(soup)
    objects = jsonld_objects(soup)

    artwork_id = slug(ARTWORK_URL_RE, url)
    artist_id = slug(ARTIST_URL_RE, url)
    if artwork_id:
        return _artwork_page(soup, objects, url, artwork_id)
    if artist_id and artist_id not in PAGE_RESERVED:
        return _artist_page(soup, objects, url, artist_id)

    if (product := first_of_type(objects, "Product", "ProductGroup")) and (
        aid := slug(ARTWORK_URL_RE, product.get("url"))
    ):
        return _artwork_page(soup, objects, url or str(product.get("url")), aid)
    if (person := first_of_type(objects, "Person")) and (
        aid := slug(ARTIST_URL_RE, person.get("url"))
    ) and aid not in PAGE_RESERVED:
        return _artist_page(soup, objects, url or str(person.get("url")), aid)

    return {"page_type": "unknown", "rows": {}}


def _artwork_page(
    soup: BeautifulSoup, objects: list[LdObject], url: str | None, artwork_id: str
) -> dict[str, Any]:
    product = first_of_type(objects, "Product") or first_of_type(objects, "ProductGroup")
    offers = _offers(product)
    artist_anchor = _artist_anchor(soup, product)
    artist_id = slug(ARTIST_URL_RE, artist_anchor.get("url"))
    artist_name = artist_anchor.get("name")
    body_text = soup.get_text(" ", strip=True)
    dims = DIM_RE.search(body_text)
    year_m = YEAR_RE.search(body_text)

    artwork = {
        "artwork_id": artwork_id,
        "artist_id": artist_id,
        "artist_name": artist_name,
        "title": (product or {}).get("name") or meta_content(soup, "og:title") or _h1(soup),
        "url": url or (product or {}).get("url"),
        "description": (product or {}).get("description") or meta_content(soup, "og:description"),
        "image_url": absolute_url(
            first_image((product or {}).get("image")) or meta_content(soup, "og:image")
        ),
        "price": meta_content(soup, "og:price:amount") or offers.get("price"),
        "currency": meta_content(soup, "og:price:currency") or offers.get("priceCurrency"),
        "in_stock": offers.get("in_stock"),
        "art_medium": _medium(soup, body_text),
        "height_in": float(dims.group("h")) if dims else None,
        "width_in": float(dims.group("w")) if dims else None,
        "year": int(year_m.group(1)) if year_m else None,
        "is_signed": True if "signed" in body_text.lower() else None,
        "has_detail": True,
    }
    artists: list[dict[str, Any]] = []
    if artist_id or artist_name:
        artists.append(
            {
                "artist_id": artist_id,
                "name": artist_name,
                "url": artist_anchor.get("url"),
                "has_page": False,
            }
        )
    return {"page_type": "artwork", "rows": {"artworks": [artwork], "artists": artists}}


def _artist_page(
    soup: BeautifulSoup, objects: list[LdObject], url: str | None, artist_id: str
) -> dict[str, Any]:
    person = first_of_type(objects, "Person")
    location = _location(soup, person)
    artist = {
        "artist_id": artist_id,
        "name": (person or {}).get("name") or _h1(soup),
        "url": url or (person or {}).get("url"),
        "avatar_url": absolute_url(
            first_image((person or {}).get("image")) or meta_content(soup, "og:image")
        ),
        "biography": (person or {}).get("description") or _artist_bio(soup),
        "city": location.get("city"),
        "region": location.get("region"),
        "country": location.get("country"),
        "has_page": True,
    }
    return {"page_type": "artist", "artist_id": artist_id, "rows": {"artists": [artist]}}


def _offers(product: LdObject | None) -> dict[str, Any]:
    if not product:
        return {}
    raw = product.get("offers")
    offer: dict[str, Any] = {}
    if isinstance(raw, dict):
        offer = raw
        nested = raw.get("offers")
        if isinstance(nested, list) and nested and isinstance(nested[0], dict):
            offer = {**raw, **nested[0]}
        elif isinstance(nested, dict):
            offer = {**raw, **nested}
    elif isinstance(raw, list) and raw and isinstance(raw[0], dict):
        offer = raw[0]
    availability = str(offer.get("availability") or "")
    return {
        "price": offer.get("price"),
        "priceCurrency": offer.get("priceCurrency"),
        "in_stock": availability.endswith("InStock") if availability else None,
    }


def _artist_anchor(soup: BeautifulSoup, product: LdObject | None) -> dict[str, str | None]:
    if product:
        for key in ("creator", "brand", "author"):
            node = product.get(key)
            if isinstance(node, dict):
                name = node.get("name") if isinstance(node.get("name"), str) else None
                url = node.get("url") if isinstance(node.get("url"), str) else None
                if url and slug(ARTIST_URL_RE, url):
                    return {"name": name, "url": url}
                if name and key == "creator":
                    return {"name": name, "url": None}
    for a in soup.find_all("a", href=True):
        href = str(a["href"])
        aid = slug(ARTIST_URL_RE, href) or slug(ARTIST_URL_RE, f"https://www.ugallery.com{href}")
        if aid and aid not in PAGE_RESERVED:
            full = href if href.startswith("http") else f"https://www.ugallery.com{href}"
            return {"name": a.get_text(" ", strip=True) or None, "url": full}
    return {"name": None, "url": None}


def _medium(soup: BeautifulSoup, body_text: str) -> str | None:
    for sel in (".product-type", ".artwork-medium", "[data-medium]"):
        tag = soup.select_one(sel)
        if tag and (text := tag.get_text(" ", strip=True)):
            return text
    lower = body_text.lower()
    for phrase in (
        "acrylic painting",
        "oil painting",
        "watercolor",
        "mixed media",
        "photography",
        "sculpture",
    ):
        if phrase in lower:
            return phrase
    return None


def _h1(soup: BeautifulSoup) -> str | None:
    tag = soup.find("h1")
    return tag.get_text(" ", strip=True) if tag else None


def _artist_bio(soup: BeautifulSoup) -> str | None:
    for sel in (".artist-bio", ".rte", "main"):
        tag = soup.select_one(sel)
        if tag:
            text = tag.get_text("\n", strip=True)
            if text and len(text) > 40:
                return text
    return None


def _location(soup: BeautifulSoup, person: LdObject | None) -> dict[str, str | None]:
    out: dict[str, str | None] = {"city": None, "region": None, "country": None}
    if person:
        loc = person.get("homeLocation") or person.get("address")
        if isinstance(loc, dict):
            addr = loc.get("address") if isinstance(loc.get("address"), dict) else loc
            if isinstance(addr, dict):
                city, region, country = (
                    addr.get("addressLocality"),
                    addr.get("addressRegion"),
                    addr.get("addressCountry"),
                )
                out["city"] = city if isinstance(city, str) else None
                out["region"] = region if isinstance(region, str) else None
                out["country"] = country if isinstance(country, str) else None
    h1 = soup.find("h1")
    sib = h1.find_next_sibling() if h1 else None
    if sib:
        text = sib.get_text(" ", strip=True)
        if text and "," in text and len(text) < 80:
            city, _, region = text.partition(",")
            out["city"] = out["city"] or city.strip()
            out["region"] = out["region"] or region.strip()
    return out

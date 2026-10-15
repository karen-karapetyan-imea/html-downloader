"""Parse one Fine Art America page (raw HTML): artwork pages (/featured/<slug>.html) and artist
profiles (/profiles/<slug>). Everything comes from JSON-LD plus the og:price meta tags.

Fine Art America sells prints of uploaded images, so the price is the one the page advertises for
its default print product (og:price), not the price of an original.
"""

import re
from typing import Any

from bs4 import BeautifulSoup, Tag

from etl_core.jsonld import LdObject, first_of_type, jsonld_objects, ld_types

ARTWORK_URL_RE = re.compile(r"/featured/([^/?#]+)\.html")
PROFILE_URL_RE = re.compile(r"/profiles/([^/?#]+)")
ARTWORK_TYPES = ("VisualArtwork", "Painting", "Photograph", "Drawing", "Sculpture")
PLACEHOLDER_IMAGE = "headshotPlaceholder"


def slug(pattern: re.Pattern[str], url: object) -> str | None:
    m = pattern.search(url) if isinstance(url, str) else None
    return m.group(1).lower() if m else None


def meta_content(soup: BeautifulSoup, prop: str) -> str | None:
    tag = soup.find("meta", property=prop)
    return str(tag["content"]) if isinstance(tag, Tag) and tag.get("content") else None


def parse_page(html: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")
    objects = jsonld_objects(soup)
    if (art := first_of_type(objects, *ARTWORK_TYPES)) is not None:
        return _artwork_page(soup, art)
    if (person := first_of_type(objects, "Person")) is not None:
        return _artist_page(person)
    return {"page_type": "unknown", "rows": {}}


def _artwork_page(soup: BeautifulSoup, art: LdObject) -> dict[str, Any]:
    creator = art.get("creator") if isinstance(art.get("creator"), dict) else {}
    artist_id = slug(PROFILE_URL_RE, creator.get("url"))
    offers = art.get("offers") if isinstance(art.get("offers"), dict) else {}
    availability = [
        str(o.get("availability") or "") for o in offers.get("offers") or [] if isinstance(o, dict)
    ]
    artwork = {
        "artwork_id": slug(ARTWORK_URL_RE, art.get("url") or art.get("@id")),
        "artist_id": artist_id,
        "title": art.get("name"),
        "url": art.get("url") or art.get("@id"),
        "art_type": next((t for t in sorted(ld_types(art)) if t != "VisualArtwork"), None),
        "art_medium": art.get("artMedium"),
        "category_path": art.get("category"),
        "description": art.get("description"),
        "image_url": art.get("image"),
        "price": meta_content(soup, "og:price:amount"),
        "currency": meta_content(soup, "og:price:currency") or offers.get("priceCurrency"),
        "low_price": offers.get("lowPrice"),
        "high_price": offers.get("highPrice"),
        "in_stock": any(a.endswith("InStock") for a in availability) if availability else None,
        "has_detail": True,
    }
    artist = {
        "artist_id": artist_id,
        "name": creator.get("name"),
        "url": creator.get("url"),
        "has_page": False,
    }
    return {"page_type": "artwork", "rows": {"artworks": [artwork], "artists": [artist] if artist_id else []}}


def _artist_page(person: LdObject) -> dict[str, Any]:
    artist_id = slug(PROFILE_URL_RE, person.get("url"))
    location = person.get("homeLocation") if isinstance(person.get("homeLocation"), dict) else {}
    address = location.get("address") if isinstance(location.get("address"), dict) else {}
    image = person.get("image")
    artist = {
        "artist_id": artist_id,
        "name": person.get("name"),
        "url": person.get("url"),
        "avatar_url": None if not isinstance(image, str) or PLACEHOLDER_IMAGE in image else image,
        "city": address.get("addressLocality"),
        "region": address.get("addressRegion"),
        "country": address.get("addressCountry"),
        "has_page": True,
    }
    return {"page_type": "artist", "artist_id": artist_id, "rows": {"artists": [artist] if artist_id else []}}

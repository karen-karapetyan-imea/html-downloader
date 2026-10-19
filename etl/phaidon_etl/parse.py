"""Parse one Phaidon product page (raw HTML).

Only art-edition products (pages with `.art-product-details`) become artworks. Books and
other shop products return page_type=unknown. There are no dedicated artist profile pages
in the crawl.
"""

from __future__ import annotations

import re
from typing import Any

from bs4 import BeautifulSoup, Tag

from etl_core.jsonld import LdObject, first_of_type, jsonld_objects

PRODUCT_URL_RE = re.compile(r"phaidon\.com/(?:[a-z]{2}(?:-[a-z]{2})?/)?products/([a-z0-9-]+)/?", re.I)
COLLECTION_URL_RE = re.compile(r"phaidon\.com/(?:[a-z]{2}(?:-[a-z]{2})?/)?collections/([a-z0-9-]+)/?", re.I)
DETAILS_RE = re.compile(
    r"Medium:\s*(?P<medium>[^:]+?)(?=\s*Dimensions:|\s*Edition size:|\s*Authentication:|$)"
    r"(?:\s*Dimensions:\s*(?P<dims>[^:]+?)(?=\s*Edition size:|\s*Authentication:|$))?"
    r"(?:\s*Edition size:\s*(?P<edition>[^:]+?)(?=\s*Authentication:|$))?"
    r"(?:\s*Authentication:\s*(?P<auth>.+))?",
    re.I | re.S,
)
DIM_IN_RE = re.compile(
    r"(?P<a>\d+(?:\.\d+)?)\s*x\s*(?P<b>\d+(?:\.\d+)?)(?:\s*x\s*(?P<c>\d+(?:\.\d+)?))?\s*inches?",
    re.I,
)
YEAR_RE = re.compile(r",\s*((?:19|20)\d{2})\s*$")


def slug(pattern: re.Pattern[str], url: object) -> str | None:
    m = pattern.search(url) if isinstance(url, str) else None
    return m.group(1).lower() if m else None


def meta_content(soup: BeautifulSoup, prop: str) -> str | None:
    tag = soup.find("meta", property=prop)
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
    if soup.select_one(".art-product-details") is None:
        return {"page_type": "unknown", "rows": {}}

    objects = jsonld_objects(soup)
    product = first_of_type(objects, "ProductGroup") or first_of_type(objects, "Product")
    url = page_url(soup) or (str(product.get("url")) if product and product.get("url") else None)
    artwork_id = slug(PRODUCT_URL_RE, url)
    if artwork_id is None:
        return {"page_type": "unknown", "rows": {}}

    variant = _first_variant(product)
    offers = variant.get("offers") if isinstance(variant.get("offers"), dict) else {}
    details = _art_details(soup)
    artist_name, artist_id, artist_url = _artist(soup)
    title = _edition_title(soup) or (product or {}).get("name") or meta_content(soup, "og:title")
    year = None
    if isinstance(title, str) and (ym := YEAR_RE.search(title)):
        year = int(ym.group(1))
        title = title[: ym.start()].rstrip(" ,")

    dims = details.get("dims") or {}
    artwork = {
        "artwork_id": artwork_id,
        "artist_id": artist_id,
        "artist_name": artist_name,
        "title": title,
        "url": url,
        "description": (product or {}).get("description") or meta_content(soup, "og:description"),
        "image_url": absolute_url(
            first_image(variant.get("image") or (product or {}).get("image"))
            or meta_content(soup, "og:image")
        ),
        "price": meta_content(soup, "og:price:amount") or offers.get("price"),
        "currency": meta_content(soup, "og:price:currency") or offers.get("priceCurrency"),
        "in_stock": _in_stock(offers.get("availability")),
        "art_medium": details.get("medium"),
        "width_in": dims.get("width_in"),
        "height_in": dims.get("height_in"),
        "depth_in": dims.get("depth_in"),
        "edition_size": details.get("edition"),
        "is_signed": details.get("is_signed"),
        "year": year,
        "has_detail": True,
    }
    artists: list[dict[str, Any]] = []
    if artist_id or artist_name:
        artists.append(
            {
                "artist_id": artist_id,
                "name": artist_name,
                "url": artist_url,
                "has_page": False,
            }
        )
    return {"page_type": "artwork", "rows": {"artworks": [artwork], "artists": artists}}


def _first_variant(product: LdObject | None) -> dict[str, Any]:
    if not product:
        return {}
    variants = product.get("hasVariant")
    if isinstance(variants, list) and variants and isinstance(variants[0], dict):
        return variants[0]
    if isinstance(variants, dict):
        return variants
    return product


def _in_stock(availability: object) -> bool | None:
    if not isinstance(availability, str) or not availability:
        return None
    return availability.endswith("InStock")


def _art_details(soup: BeautifulSoup) -> dict[str, Any]:
    tag = soup.select_one(".art-product-details-inner") or soup.select_one(".art-product-details")
    text = tag.get_text(" ", strip=True) if tag else ""
    m = DETAILS_RE.search(text) if text else None
    medium = m.group("medium").strip() if m and m.group("medium") else None
    edition = m.group("edition").strip() if m and m.group("edition") else None
    auth = m.group("auth").strip() if m and m.group("auth") else None
    dims_text = m.group("dims").strip() if m and m.group("dims") else None
    dims: dict[str, float] = {}
    if dims_text and (dm := DIM_IN_RE.search(dims_text)):
        a, b, c = float(dm.group("a")), float(dm.group("b")), dm.group("c")
        # Phaidon editions list height x width (and optional depth), matching the probe sample.
        dims = {"height_in": a, "width_in": b}
        if c is not None:
            dims["depth_in"] = float(c)
    return {
        "medium": medium,
        "edition": edition,
        "is_signed": True if auth and "signed" in auth.lower() else (False if auth else None),
        "dims": dims,
    }


def _edition_title(soup: BeautifulSoup) -> str | None:
    tag = soup.select_one(".artwork") or soup.select_one("h1")
    return tag.get_text(" ", strip=True) if tag else None


def _artist(soup: BeautifulSoup) -> tuple[str | None, str | None, str | None]:
    author = soup.select_one(".product-author .author") or soup.select_one(".product-author")
    name = author.get_text(" ", strip=True) if author else None
    # Prefer a creator collection link near the product (e.g. /collections/george-lois).
    for a in soup.select("a[href*='/collections/']"):
        href = str(a.get("href") or "")
        cid = slug(COLLECTION_URL_RE, href) or slug(COLLECTION_URL_RE, f"https://www.phaidon.com{href}")
        if not cid or cid.startswith(("all-", "shop-", "new-", "fall-", "books", "bestsellers")):
            continue
        label = a.get_text(" ", strip=True)
        if (name and name.lower() in label.lower()) or (
            label and len(label) < 60 and "shop" not in label.lower()
        ):
            full = href if href.startswith("http") else f"https://www.phaidon.com{href}"
            return name or label, cid, full
    return name, None, None

"""Christie's lot pages: `window.chrComponents.lotHeader_*` JSON.

The lot id is `object_id` (`/en/lot/lot-{objectId}`). Currency is the code in
`price_realised_txt` / `estimate_txt` (`GBP 44,100`); the payload has no separate currency field.
`title_primary_txt` is the artist and `title_secondary_txt` is the work. Old lots have
placeholder primaries ("No Description (2)", "1 ^,,^") and a `0001-01-01` start date, so the
sale date falls back to `end_date`.

The description is the server-rendered "Details" section: catalogue lines `Artist (Nationality,
1860-1954)` / title / `signed ...` / medium / size. Its first line gives the artist's
nationality and life dates when it starts with the artist. The sale location is `location_txt`
without the saleroom ("London (South Kensington)" -> "London"); the department sits in the
page's `AnalyticsDataLayer` on recent lots only.
"""

from __future__ import annotations

import html as html_lib
import json
import re
from typing import Any

from lots import catalogue
from lots.embedded import loads_balanced
from lots.fields import lot_page, sale_status, to_date, unknown_page

_HEADER = re.compile(r"window\.chrComponents\.lotHeader_\d+\s*=\s*")
_CURRENCY = re.compile(r"\b([A-Z]{3})\b")
_PLACEHOLDER = re.compile(r"^no description\b|\^,,\^", re.IGNORECASE)
_DETAILS = re.compile(
    r'<div slot="header">\s*Details\s*</div>\s*<div slot="content"[^>]*>\s*'
    r'<span class="chr-lot-section__accordion--text">(.*?)</span>',
    re.DOTALL,
)
_BREAK = re.compile(r"[ \t]*<br\s*/?>[ \t]*\n?", re.IGNORECASE)
_TAG = re.compile(r"<[^>]+>")
_DEPARTMENT = re.compile(r'var AnalyticsDataLayer\s*=\s*\{"page":\{[^{}]*?"department":"([^"]*)"')
_SALEROOM = re.compile(r"\s*\([^()]*\)\s*$")
_SQUASHED = {"NewYorkChristie'sEast": "New York", "UnitedArabEmirates": "United Arab Emirates"}
_MAX_DEPARTMENTS = 3


def parse_page(html: str, url: str | None = None) -> dict[str, Any]:
    data = _header(html)
    if data is None:
        return unknown_page()
    lots = data.get("lots")
    lot = lots[0] if isinstance(lots, list) and lots and isinstance(lots[0], dict) else None
    if lot is None or lot.get("object_id") is None:
        return unknown_page()
    sale = data.get("sale") if isinstance(data.get("sale"), dict) else {}
    unsold = lot.get("is_unsold") is True
    price = None if unsold or lot.get("price_on_request") is True else lot.get("price_realised")
    over = sale.get("is_auction_over") is True
    artist = _primary(lot.get("title_primary_txt"))
    description = _details(html)
    born, died, nationality = _life(artist, description)
    return lot_page(
        platform_lot_id=str(lot.get("object_id")).strip(),
        lot_url=_text(lot.get("url")) or url,
        lot_number=_text(lot.get("lot_id_txt")),
        title=_text(lot.get("title_secondary_txt")),
        description=description,
        artist_name=artist,
        artist_birth_year=born,
        artist_death_year=died,
        artist_nationality=nationality,
        image_url=_image(lot.get("lot_assets")),
        currency=_currency(lot.get("price_realised_txt"), lot.get("estimate_txt")),
        low_estimate=None if lot.get("estimate_on_request") else lot.get("estimate_low"),
        high_estimate=None if lot.get("estimate_on_request") else lot.get("estimate_high"),
        price_realized=price,
        sale_status=sale_status(
            sold=_positive(price), closed=unsold or (over and not _positive(price))
        ),
        sale_id=_text(sale.get("sale_number")) or _text(sale.get("sale_id")),
        sale_title=_text(sale.get("title_txt")),
        sale_date=to_date(sale.get("start_date")) or to_date(sale.get("end_date")),
        sale_url=_text(sale.get("url")),
        sale_location=_location(sale.get("location_txt")),
        department=_department(html),
        is_result=over or _positive(price) or unsold,
    )


def _details(html: str) -> str | None:
    """The "Details" section as text, one catalogue line per line."""
    match = _DETAILS.search(html)
    if match is None:
        return None
    text = html_lib.unescape(_TAG.sub("", _BREAK.sub("\n", match.group(1))))
    return _text("\n".join(" ".join(line.split()) for line in text.splitlines()).strip())


def _life(artist: str | None, description: str | None) -> tuple[int | None, int | None, str | None]:
    """(birth, death, nationality) from the artist's own catalogue line, else from the header's
    `Name (1887-1985)`. The first Details line is the artist's only when it starts with the name."""
    if artist is None:
        return None, None, None
    first = description.split("\n", 1)[0].strip() if description else ""
    name = artist.split("(", 1)[0].strip().casefold()
    if name and first.casefold().startswith(name):
        born, died, nationality = catalogue.life(first)
        if born is not None:
            return born, died, nationality
    return catalogue.life(artist)


def _location(value: object) -> str | None:
    text = _text(value)
    if text is None:
        return None
    text = _SQUASHED.get(text, text)
    return _SALEROOM.sub("", text) or None


def _department(html: str) -> str | None:
    match = _DEPARTMENT.search(html)
    if match is None:
        return None
    names = list(dict.fromkeys(n.strip() for n in match.group(1).split(";") if n.strip()))
    if not names or len(names) > _MAX_DEPARTMENTS:
        return None
    return "; ".join(names)


def _primary(value: object) -> str | None:
    text = _text(value)
    if text is None or _PLACEHOLDER.search(text):
        return None
    return text


def _header(html: str) -> dict[str, Any] | None:
    match = _HEADER.search(html)
    if match is None:
        return None
    start = match.end()
    while start < len(html) and html[start].isspace():
        start += 1
    if start >= len(html) or html[start] != "{":
        return None
    try:
        payload = loads_balanced(html, start)
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    data = payload.get("data")
    return data if isinstance(data, dict) else payload


def _image(assets: object) -> str | None:
    if not isinstance(assets, list):
        return None
    for asset in assets:
        if isinstance(asset, dict) and isinstance(asset.get("image_url"), str):
            return asset["image_url"]
    return None


def _currency(*values: object) -> str | None:
    for value in values:
        if isinstance(value, str):
            match = _CURRENCY.search(value)
            if match:
                return match.group(1)
    return None


def _positive(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and value > 0


def _text(value: object) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip()
    return text or None

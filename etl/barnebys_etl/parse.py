"""Barnebys lot pages: the lot object inside the Next.js flight payload.

Live and realised pages share the numeric id at the end of `lot.id`. A realised URL
(`/realized-prices/` or `/realised-prices/`) wins inside one crawl via `is_result`.
Barnebys publishes a single `estimatePrice`, stored as the low estimate.
"""

from __future__ import annotations

import re
from typing import Any

from lots.embedded import first_json_object, next_flight_chunks
from lots.fields import lot_page, sale_status, to_date, unknown_page

_ID_SUFFIX = re.compile(r"(\d+)$")
_FLIGHT_REF = re.compile(r"^\$[0-9a-fA-F]+$")
_REALISED = ("/realized-prices/", "/realised-prices/")
_PRICE_KEYS = ("soldPrice", "price", "hammerPrice", "realizedPrice", "realisedPrice", "finalPrice")


def parse_page(html: str, url: str | None = None) -> dict[str, Any]:
    lot = first_json_object(next_flight_chunks(html), "lot")
    if lot is None or lot.get("id") is None:
        return unknown_page()
    house = lot.get("auctionHouse") if isinstance(lot.get("auctionHouse"), dict) else {}
    realised = _is_realised(url) or _is_realised(_text(lot.get("canonicalUrl")))
    price = _positive(lot, _PRICE_KEYS)
    end = to_date(lot.get("auctionEndDate"))
    return lot_page(
        platform_lot_id=_numeric_id(lot.get("id")),
        lot_url=url,
        title=_text(lot.get("title")),
        description=_description(lot.get("description")),
        category=_text(lot.get("categoryName")),
        image_url=_text(lot.get("imageUrl")),
        currency=_text(lot.get("currency")),
        low_estimate=lot.get("estimatePrice"),
        price_realized=price,
        sale_status=sale_status(sold=price is not None, closed=realised and price is None),
        sale_date=end,
        selling_house_name=_text(house.get("name")),
        is_result=realised or price is not None,
    )


def _numeric_id(value: object) -> str | None:
    if isinstance(value, bool) or value is None:
        return None
    text = str(value).strip()
    match = _ID_SUFFIX.search(text)
    return match.group(1) if match else None


def _is_realised(url: str | None) -> bool:
    if not url:
        return False
    lowered = url.lower()
    return any(part in lowered for part in _REALISED)


def _description(value: object) -> str | None:
    text = _text(value)
    if text is None or _FLIGHT_REF.match(text):
        return None
    return text


def _positive(lot: dict[str, Any], keys: tuple[str, ...]) -> int | float | None:
    for key in keys:
        value = lot.get(key)
        if isinstance(value, int | float) and not isinstance(value, bool) and value > 0:
            return value
    return None


def _text(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None

"""Christie's lot pages: `window.chrComponents.lotHeader_*` JSON.

The lot id is `object_id` (`/en/lot/lot-{objectId}`). Currency is the code in
`price_realised_txt` / `estimate_txt` (`GBP 44,100`); the payload has no separate currency field.
`title_primary_txt` is the artist and `title_secondary_txt` is the work.
"""

from __future__ import annotations

import json
import re
from typing import Any

from lots.embedded import loads_balanced
from lots.fields import lot_page, sale_status, to_date, unknown_page

_HEADER = re.compile(r"window\.chrComponents\.lotHeader_\d+\s*=\s*")
_CURRENCY = re.compile(r"\b([A-Z]{3})\b")


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
    return lot_page(
        platform_lot_id=str(lot.get("object_id")).strip(),
        lot_url=_text(lot.get("url")) or url,
        lot_number=_text(lot.get("lot_id_txt")),
        title=_text(lot.get("title_secondary_txt")),
        artist_name=_text(lot.get("title_primary_txt")),
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
        sale_date=to_date(sale.get("start_date")),
        sale_url=_text(sale.get("url")),
        is_result=over or _positive(price) or unsold,
    )


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

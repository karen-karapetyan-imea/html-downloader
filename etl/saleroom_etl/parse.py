"""The Saleroom lot pages: the `LotDetailsViewModel` object pushed onto `dataLayer`.

Estimates are decimal strings (`minEstimate` / `maxEstimate`). Currency sits in the
visible estimate cell (`100 GBP - 150 GBP`), not in that object. `openingPrice` is the
next bid, not a hammer.
"""

from __future__ import annotations

import re
from typing import Any

from lots.fields import lot_page, sale_status, unknown_page

_PAIR = re.compile(r'"([A-Za-z0-9_]+)"\s*:\s*"((?:\\.|[^"\\])*)"')
_ESTIMATE = re.compile(r"([\d,.]+)\s+([A-Z]{3})\s*-\s*[\d,.]+\s+[A-Z]{3}")
_SOLD_FOR = re.compile(r"Sold for\s+([\d,.]+)\s+([A-Z]{3})", re.IGNORECASE)
_META = re.compile(
    r'<meta[^>]+property=["\'](?P<prop>og:[^"\']+)["\'][^>]+content=["\'](?P<content>[^"\']*)["\']'
    r'|<meta[^>]+content=["\'](?P<content2>[^"\']*)["\'][^>]+property=["\'](?P<prop2>og:[^"\']+)["\']',
    re.IGNORECASE,
)
_PRICE_KEYS = ("hammerPrice", "priceRealised", "realisedPrice", "soldPrice")


def parse_page(html: str, url: str | None = None) -> dict[str, Any]:
    fields = _view_model(html)
    lot_id = fields.get("lotId")
    if not lot_id:
        return unknown_page()
    meta = _meta(html)
    currency = _currency(html)
    price, price_currency = _realised(fields, html)
    status = sale_status(fields.get("lotStatus"), sold=price is not None)
    catalogue = fields.get("catalogueId")
    lot_url = meta.get("og:url") or url
    return lot_page(
        platform_lot_id=lot_id.lower(),
        lot_url=lot_url,
        title=fields.get("lotName"),
        description=meta.get("og:description"),
        category=fields.get("lotPrimaryCategory"),
        image_url=meta.get("og:image"),
        currency=price_currency or currency,
        low_estimate=_amount(fields.get("minEstimate")),
        high_estimate=_amount(fields.get("maxEstimate")),
        price_realized=price,
        sale_status=status,
        sale_id=catalogue,
        sale_title=fields.get("catalogueName"),
        sale_date=fields.get("lotStartDate") or None,
        sale_url=_catalogue_url(lot_url),
        selling_house_name=fields.get("auctioneer"),
        is_result=status in {"sold", "passed", "unsold"} or price is not None,
    )


def _view_model(html: str) -> dict[str, str]:
    anchor = html.find("LotDetailsViewModel")
    if anchor < 0:
        return {}
    return dict(_PAIR.findall(html[anchor : anchor + 6000]))


def _meta(html: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for match in _META.finditer(html):
        prop = match.group("prop") or match.group("prop2")
        content = match.group("content") or match.group("content2")
        if prop and content:
            found[prop.lower()] = content
    return found


def _currency(html: str) -> str | None:
    match = _ESTIMATE.search(html)
    return match.group(2) if match else None


def _realised(fields: dict[str, str], html: str) -> tuple[str | None, str | None]:
    for key in _PRICE_KEYS:
        amount = _amount(fields.get(key))
        if amount is not None:
            return amount, None
    match = _SOLD_FOR.search(html)
    if match is None:
        return None, None
    return _amount(match.group(1)), match.group(2)


def _amount(value: str | None) -> str | None:
    if not value:
        return None
    try:
        number = float(value.replace(",", ""))
    except ValueError:
        return None
    return value.replace(",", "") if number > 0 else None


def _catalogue_url(lot_url: str | None) -> str | None:
    if not lot_url or "/lot-" not in lot_url:
        return None
    return lot_url.split("/lot-", 1)[0]

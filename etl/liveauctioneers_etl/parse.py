"""LiveAuctioneers price-result pages.

Current pages embed the lot in `<script id="__SERVER_DATA__">`. Older pages assign the same
shape to `window.__data`. The id is the `/price-result/{slug}` slug. `salePrice` counts only
when `isSold` is true. The sale location is the house's city (`sellerCity`); `lotLocation` is
where the item is stored.
"""

from __future__ import annotations

from typing import Any

from lots.embedded import assignment_json, script_json
from lots.fields import dig, lot_page, sale_status, to_date, unknown_page


def parse_page(html: str, url: str | None = None) -> dict[str, Any]:
    state = script_json(html, "__SERVER_DATA__")
    if not isinstance(state, dict):
        state = assignment_json(html, "window.__data")
    data = state.get("data") if isinstance(state, dict) else None
    item = _item(data, url)
    if item is None:
        return unknown_page()
    slug = _text(item.get("slug"))
    if slug is None:
        return unknown_page()
    sold = item.get("isSold") is True
    passed = item.get("isPassed") is True
    done = _text(item.get("catalogStatus")) == "done"
    return lot_page(
        platform_lot_id=slug.lower(),
        lot_url=url,
        lot_number=_text(item.get("lotNumber")),
        title=_text(item.get("title")),
        description=_text(item.get("shortDescription")),
        currency=_text(item.get("currency")),
        low_estimate=item.get("lowBidEstimate"),
        high_estimate=item.get("highBidEstimate"),
        price_realized=item.get("salePrice") if sold else None,
        sale_status=sale_status(sold=sold, passed=passed, closed=done and not sold and not passed),
        sale_id=_text(item.get("catalogId")),
        sale_title=_text(item.get("catalogTitle")),
        sale_date=to_date(item.get("saleStartTs")),
        selling_house_name=_text(item.get("sellerName")),
        sale_location=_city(item.get("sellerCity")),
        is_result=True,
    )


def _item(data: object, url: str | None) -> dict[str, Any] | None:
    if not isinstance(data, dict):
        return None
    by_id = dig(data, "itemSummary", "byId")
    if not isinstance(by_id, dict):
        return None
    slug_id = dig(data, "priceResults", "slugItemId")
    if slug_id is not None and isinstance(by_id.get(str(slug_id)), dict):
        return by_id[str(slug_id)]
    slug = _slug(url)
    if slug is None:
        return None
    for row in by_id.values():
        if isinstance(row, dict) and _text(row.get("slug")) == slug:
            return row
    return None


def _slug(url: str | None) -> str | None:
    if not url or "/price-result/" not in url:
        return None
    return url.split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1] or None


def _city(value: object) -> str | None:
    """Some houses type their city in capitals ("PORT ORANGE")."""
    text = _text(value)
    return text.title() if text and text.isupper() else text


def _text(value: object) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip()
    return text or None

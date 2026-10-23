"""Invaluable lot pages: `window.__PRELOADED_STATE__` (`pdp.lotData`).

The id is `lotRef` (the hex id in `/auction-lot/...-c-{lotRef}`). `soldAmount` of 0 on an
open lot is not a hammer. The named seller is the auction house on the page, not Invaluable.
"""

from __future__ import annotations

from typing import Any

from lots.embedded import assignment_json
from lots.fields import lot_page, sale_status, to_date, unknown_page


def parse_page(html: str, url: str | None = None) -> dict[str, Any]:
    state = assignment_json(html, "window.__PRELOADED_STATE__")
    pdp = state.get("pdp") if isinstance(state, dict) else None
    if not isinstance(pdp, dict):
        return unknown_page()
    lot = pdp.get("lotData")
    lot_ref = _text(lot.get("lotRef")) if isinstance(lot, dict) else None
    if not isinstance(lot, dict) or lot_ref is None:
        return unknown_page()
    catalog = pdp.get("catalogData") if isinstance(pdp.get("catalogData"), dict) else {}
    house = pdp.get("auctionHouseData") if isinstance(pdp.get("auctionHouseData"), dict) else {}
    artist = pdp.get("artistInfo") if isinstance(pdp.get("artistInfo"), dict) else {}
    sold = lot.get("isLotSold") is True
    passed = lot.get("isLotPassed") is True
    closed = lot.get("isLotClosed") is True
    ref = _text(catalog.get("ref"))
    return lot_page(
        platform_lot_id=lot_ref.lower(),
        lot_url=url,
        lot_number=_text(lot.get("lotNumber")),
        title=_text(lot.get("lotTitle")),
        description=_text(lot.get("lotDescription")),
        artist_name=_text(artist.get("fullName")),
        category=_text(catalog.get("supercategory")) or _text(catalog.get("category")),
        image_url=_image(lot.get("photos")),
        currency=_text(lot.get("currency")),
        low_estimate=lot.get("estimateLow"),
        high_estimate=lot.get("estimateHigh"),
        price_realized=lot.get("soldAmount") if sold else None,
        sale_status=sale_status(sold=sold, passed=passed, closed=closed and not sold),
        sale_id=ref,
        sale_title=_text(catalog.get("title")),
        sale_date=to_date(catalog.get("eventDate")),
        sale_url=f"https://www.invaluable.com/catalog/{ref}" if ref else None,
        selling_house_name=_text(house.get("name")),
        is_result=sold or passed or closed,
    )


def _image(photos: object) -> str | None:
    if not isinstance(photos, list) or not photos or not isinstance(photos[0], dict):
        return None
    photo = photos[0]
    for key in ("large", "medium"):
        url = photo.get(key)
        if isinstance(url, str) and url:
            return url
    return None


def _text(value: object) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip()
    return text or None

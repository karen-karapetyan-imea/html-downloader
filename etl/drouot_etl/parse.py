"""Drouot lot pages: the SvelteKit `data:{lot:{...}}` object literal.

There is no separate title field; the first line of `description` is the catalogue title.
`result == 0` is an empty hammer (the lot is not sold yet), not a price of zero.
The selling house is the auctioneer on the page, which is not Drouot itself.
"""

from __future__ import annotations

import html as html_lib
from typing import Any

from lots.embedded import parse_js_value
from lots.fields import dig, lot_page, sale_status, to_date, unknown_page

_MARKERS = ("data:{lot:{", "lot:{")


def parse_page(html: str, url: str | None = None) -> dict[str, Any]:
    payload = _payload(html)
    if payload is None:
        return unknown_page()
    lot = payload.get("lot") if isinstance(payload.get("lot"), dict) else payload
    if not isinstance(lot, dict) or lot.get("id") is None:
        return unknown_page()
    sale = lot.get("saleInfo") if isinstance(lot.get("saleInfo"), dict) else {}
    description = lot.get("description") if isinstance(lot.get("description"), str) else None
    result = lot.get("result")
    sold = isinstance(result, int | float) and not isinstance(result, bool) and result > 0
    sale_id = _text(lot.get("saleId")) or _text(sale.get("saleId"))
    slug = _text(sale.get("saleSlug"))
    canonical = _text(dig(payload, "metaData", "canonicalUrl"))
    return lot_page(
        platform_lot_id=_text(lot.get("id")),
        lot_url=canonical or url,
        lot_number=_text(lot.get("num")),
        title=_first_line(description),
        description=description,
        category=_text(dig(payload, "structuredData", "category")),
        image_url=_image(payload, lot),
        currency=_text(lot.get("currencyId")),
        low_estimate=lot.get("lowEstim"),
        high_estimate=lot.get("highEstim"),
        price_realized=result if sold else None,
        sale_status=sale_status(sale.get("status") or lot.get("saleStatus"), sold=sold),
        sale_id=sale_id,
        sale_title=_text(sale.get("title")),
        sale_date=to_date(sale.get("date") or lot.get("date")),
        sale_url=_sale_url(sale_id, slug),
        selling_house_name=_text(dig(sale, "auctioneerCard", "link", "auctioneerName")),
        is_result=sold,
    )


def _payload(html: str) -> dict[str, Any] | None:
    for marker in _MARKERS:
        start = 0
        while True:
            found = html.find(marker, start)
            if found < 0:
                break
            brace = found + (len("data:") if marker.startswith("data:") else len("lot:"))
            try:
                value = parse_js_value(html, brace)
            except ValueError:
                start = found + len(marker)
                continue
            lot = value.get("lot") if isinstance(value, dict) else None
            record = lot if isinstance(lot, dict) else value
            if isinstance(record, dict) and record.get("id") is not None and "lowEstim" in record:
                return value if isinstance(lot, dict) else {"lot": record}
            start = found + len(marker)
    return None


def _first_line(description: str | None) -> str | None:
    if not description:
        return None
    for line in html_lib.unescape(description).splitlines():
        text = " ".join(line.split())
        if text:
            return text
    return None


def _image(payload: dict[str, Any], lot: dict[str, Any]) -> str | None:
    image = dig(payload, "structuredData", "image")
    if isinstance(image, str) and image:
        return image
    path = dig(lot, "photo", "path")
    if isinstance(path, str) and path:
        return f"https://cdn.drouot.com/d/lot/ftall/{path}"
    return None


def _sale_url(sale_id: str | None, slug: str | None) -> str | None:
    if not sale_id:
        return None
    suffix = f"-{slug}" if slug else ""
    return f"https://drouot.com/en/v/{sale_id}{suffix}"


def _text(value: object) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip()
    return text or None

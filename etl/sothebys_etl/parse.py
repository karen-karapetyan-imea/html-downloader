"""Sotheby's lot pages: `__NEXT_DATA__` Apollo cache.

The realised price is `premiums.finalPriceV2`. The id is the URL path
`{year}/{auctionSlug}/{lotSlug}`, which is what the downloader stores, not the Apollo UUID.
Legacy `/en/auctions/ecatalogue/` pages do not carry this cache and are skipped.
"""

from __future__ import annotations

from typing import Any

from lots.embedded import script_json
from lots.fields import lot_page, sale_status, to_date, unknown_page


def parse_page(html: str, url: str | None = None) -> dict[str, Any]:
    payload = script_json(html, "__NEXT_DATA__")
    cache = _cache(payload)
    if cache is None:
        return unknown_page()
    lot = next(
        (row for key, row in cache.items() if key.startswith("LotV2:") and isinstance(row, dict)),
        None,
    )
    if lot is None:
        return unknown_page()
    auction = _deref(cache, lot.get("auction")) or {}
    bid = _deref(cache, lot.get("bidState")) or {}
    withdrawn = _deref(cache, lot.get("withdrawnState")) or {}
    slug = auction.get("slug") if isinstance(auction.get("slug"), dict) else {}
    year = _text(slug.get("year"))
    auction_slug = _text(slug.get("name"))
    lot_slug = _text(lot.get("slug"))
    sold_block = bid.get("sold") if isinstance(bid.get("sold"), dict) else {}
    sold = sold_block.get("isSold") is True
    price = _amount(sold_block.get("premiums"), "finalPriceV2") if sold else None
    estimate = lot.get("estimateV2") if isinstance(lot.get("estimateV2"), dict) else {}
    is_withdrawn = _text(withdrawn.get("state")) not in {None, "", "NotAffected"}
    closed = _text(auction.get("state")) == "Closed"
    return lot_page(
        platform_lot_id=_lot_id(year, auction_slug, lot_slug, _text(lot.get("lotId"))),
        lot_url=_lot_url(year, auction_slug, lot_slug) or url,
        lot_number=_text(_deref_field(lot.get("lotNumber"), "lotDisplayNumber")),
        title=_text(lot.get("title")),
        description=_text(lot.get("description")),
        artist_name=_text(lot.get("creatorsDisplayTitle")),
        image_url=_image(lot),
        currency=_text(auction.get("currencyV2")) or _text(auction.get("currency")),
        low_estimate=_amount(estimate, "lowEstimate"),
        high_estimate=_amount(estimate, "highEstimate"),
        price_realized=price,
        sale_status=sale_status(
            auction.get("state"), sold=sold, withdrawn=is_withdrawn, closed=closed and not sold
        ),
        sale_id=_text(lot.get("sapAuctionSaleNumber")) or _text(auction.get("sapSaleNumber")),
        sale_title=_text(auction.get("title")),
        sale_date=to_date(_dates(auction).get("closed") or _dates(auction).get("acceptsBids")),
        sale_url=_sale_url(year, auction_slug),
        is_result=sold or closed or is_withdrawn,
    )


def _cache(payload: object) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    props = payload.get("props")
    page = props.get("pageProps") if isinstance(props, dict) else None
    cache = page.get("apolloCache") if isinstance(page, dict) else None
    return cache if isinstance(cache, dict) else None


def _deref(cache: dict[str, Any], node: object) -> dict[str, Any] | None:
    if isinstance(node, dict) and isinstance(node.get("__ref"), str):
        target = cache.get(node["__ref"])
        return target if isinstance(target, dict) else None
    return node if isinstance(node, dict) else None


def _deref_field(node: object, key: str) -> object:
    if isinstance(node, dict):
        return node.get(key)
    return node


def _amount(node: object, key: str) -> object:
    if not isinstance(node, dict):
        return None
    amount = node.get(key)
    if isinstance(amount, dict):
        return amount.get("amount", amount.get("value"))
    return amount


def _image(lot: dict[str, Any]) -> str | None:
    media = next((value for key, value in lot.items() if key == "media" or key.startswith("media(")), None)
    images = media.get("images") if isinstance(media, dict) else None
    if not isinstance(images, list) or not images or not isinstance(images[0], dict):
        return None
    renditions = images[0].get("renditions")
    if not isinstance(renditions, list):
        return None
    for rendition in renditions:
        if isinstance(rendition, dict) and isinstance(rendition.get("url"), str):
            return rendition["url"]
    return None


def _dates(auction: dict[str, Any]) -> dict[str, Any]:
    dates = auction.get("dates")
    return dates if isinstance(dates, dict) else {}


def _lot_id(
    year: str | None,
    auction_slug: str | None,
    lot_slug: str | None,
    lot_id: str | None,
) -> str | None:
    if year and auction_slug and lot_slug:
        return f"{year}/{auction_slug}/{lot_slug}"
    return lot_id


def _lot_url(year: str | None, auction_slug: str | None, lot_slug: str | None) -> str | None:
    lot_id = _lot_id(year, auction_slug, lot_slug, None)
    if lot_id is None:
        return None
    return f"https://www.sothebys.com/en/buy/auction/{lot_id}"


def _sale_url(year: str | None, auction_slug: str | None) -> str | None:
    if not year or not auction_slug:
        return None
    return f"https://www.sothebys.com/en/buy/auction/{year}/{auction_slug}"


def _text(value: object) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip()
    return text or None

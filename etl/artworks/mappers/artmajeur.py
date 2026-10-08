from collections.abc import Mapping
from datetime import datetime
from functools import partial
from typing import Any

from artworks.mappers._common import Record, artist_names, collect, priced, rows_of
from artworks.schema import ArtworkRow
from etl_core.normalize import (
    clean_text,
    country_name,
    plain_text,
    platform_id,
    sane_year,
    str_list,
    to_bool,
    to_float,
    to_url,
)

_PAGES = frozenset({"artist", "artwork"})
# Parser availability values: for_sale | sold | not_for_sale | price_on_request | reproductions_only
_FOR_SALE = {"for_sale": True, "sold": False, "not_for_sale": False, "reproductions_only": False}


def is_relevant(parsed: Mapping[str, Any]) -> bool:
    return parsed.get("page_type") in _PAGES


def to_rows(parsed: Mapping[str, Any], source_file: str, crawled_at: datetime | None) -> list[ArtworkRow]:
    build = partial(_row, names=artist_names(parsed))
    return collect(rows_of(parsed, "artworks"), build, source_file, crawled_at)


def _row(w: Record, stamp: dict[str, Any], *, names: dict[Any, str]) -> ArtworkRow | None:
    artwork_id = platform_id(w.get("artwork_id"))
    if artwork_id is None:
        return None
    price, currency = priced((w.get("price_usd"), "USD"))
    availability = clean_text(w.get("availability"))
    return ArtworkRow(
        platform_artwork_id=artwork_id,
        title=clean_text(w.get("title")),
        artist_name=names.get(w.get("artist_id")),
        platform_artist_id=platform_id(w.get("artist_id")),
        artwork_url=to_url(w.get("url")),
        category=clean_text(w.get("category")),
        width_cm=to_float(w.get("width_cm")),
        height_cm=to_float(w.get("height_cm")),
        depth_cm=to_float(w.get("depth_cm")),
        price=price,
        currency=currency,
        year=sane_year(w.get("year")),
        subject=clean_text(w.get("theme")),
        description=plain_text(w.get("description")),
        styles=str_list(w.get("style")),
        mediums=str_list(w.get("medium")),
        width_in=to_float(w.get("width_in")),
        height_in=to_float(w.get("height_in")),
        depth_in=to_float(w.get("depth_in")),
        is_available_for_sale=_FOR_SALE.get(availability) if availability else None,
        ships_from_country=country_name(w.get("ships_from_country")),
        dimensions=clean_text(w.get("dimensions")),
        is_sold=availability == "sold" if availability else None,
        is_price_on_request=availability == "price_on_request" if availability else None,
        image_url=to_url(w.get("image_url")),
        technique=clean_text(w.get("technique")),
        support=clean_text(w.get("support")),
        is_ai_generated=to_bool(w.get("is_ai_generated")),
        has_certificate=to_bool(w.get("has_certificate")),
        is_signed=to_bool(w.get("is_signed")),
        has_detail=bool(w.get("has_detail")),
        **stamp,
    )

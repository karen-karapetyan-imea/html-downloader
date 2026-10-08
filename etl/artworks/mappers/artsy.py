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


def is_relevant(parsed: Mapping[str, Any]) -> bool:
    return parsed.get("page_type") in _PAGES


def to_rows(parsed: Mapping[str, Any], source_file: str, crawled_at: datetime | None) -> list[ArtworkRow]:
    build = partial(_row, names=artist_names(parsed))
    return collect(rows_of(parsed, "artworks"), build, source_file, crawled_at)


def _row(w: Record, stamp: dict[str, Any], *, names: dict[Any, str]) -> ArtworkRow | None:
    # The slug is the only id that artist-page cards and artwork pages share.
    slug = platform_id(w.get("slug"))
    if slug is None:
        return None
    price, currency = priced((w.get("price_amount"), w.get("price_currency")))
    availability = clean_text(w.get("availability"))
    return ArtworkRow(
        platform_artwork_id=slug,
        title=clean_text(w.get("title")),
        artist_name=clean_text(w.get("artist_names")) or names.get(w.get("artist_id")),
        platform_artist_id=platform_id(w.get("artist_id")),
        artwork_url=to_url(w.get("url")),
        category=clean_text(w.get("category")),
        width_cm=to_float(w.get("width_cm")),
        height_cm=to_float(w.get("height_cm")),
        depth_cm=to_float(w.get("depth_cm")),
        price=price,
        currency=currency,
        year=sane_year(w.get("year")),
        description=plain_text(w.get("description_html")),
        mediums=str_list(w.get("medium")),
        dimensions_cm=clean_text(w.get("dimensions_cm")),
        dimensions_in=clean_text(w.get("dimensions_in")),
        is_available_for_sale=availability.lower() == "for sale" if availability else None,
        ships_from_country=_origin_country(w.get("shipping_origin")),
        is_sold=to_bool(w.get("is_sold")),
        image_url=to_url(w.get("image_url")),
        has_certificate=to_bool(w.get("has_certificate_of_authenticity")),
        signature=clean_text(w.get("signature")),
        has_detail=bool(w.get("has_detail")),
        **stamp,
    )


def _origin_country(value: object) -> str | None:
    """'New York, NY, US' -> 'United States': the country is the last comma-separated part."""
    text = clean_text(value)
    return country_name(text.rsplit(",", 1)[-1]) if text else None

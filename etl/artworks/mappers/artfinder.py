from collections.abc import Mapping
from datetime import datetime
from functools import partial
from typing import Any

from artworks.mappers._common import Record, artist_names, collect, priced, rows_of
from artworks.schema import ArtworkRow
from etl_core.normalize import (
    clean_text,
    plain_text,
    platform_id,
    sane_year,
    str_list,
    to_bool,
    to_float,
    to_url,
)

_PAGES = frozenset({"artist", "artwork", "listing"})


def is_relevant(parsed: Mapping[str, Any]) -> bool:
    return parsed.get("page_type") in _PAGES


def to_rows(parsed: Mapping[str, Any], source_file: str, crawled_at: datetime | None) -> list[ArtworkRow]:
    build = partial(_row, names=artist_names(parsed))
    return collect(rows_of(parsed, "artworks"), build, source_file, crawled_at)


def _row(w: Record, stamp: dict[str, Any], *, names: dict[Any, str]) -> ArtworkRow | None:
    artwork_id = platform_id(w.get("artwork_id"))
    if artwork_id is None:
        return None
    # Artwork pages carry the seller's own currency; cards only the converted per-currency prices.
    price, currency = priced(
        (w.get("original_amount"), w.get("original_currency")), (w.get("price_usd"), "USD")
    )
    return ArtworkRow(
        platform_artwork_id=artwork_id,
        title=clean_text(w.get("name")),
        artist_name=names.get(w.get("artist_id")),
        platform_artist_id=platform_id(w.get("artist_id")),
        artwork_url=to_url(w.get("url")),
        category=clean_text(w.get("category_name")) or clean_text(w.get("category_slug")),
        width_cm=to_float(w.get("width_cm")),
        height_cm=to_float(w.get("height_cm")),
        depth_cm=to_float(w.get("depth_cm")),
        price=price,
        currency=currency,
        year=sane_year(w.get("year_made")),
        subject=clean_text(w.get("subject_name")) or clean_text(w.get("subject_slug")),
        description=plain_text(w.get("description")),
        styles=str_list(clean_text(w.get("style_name")) or w.get("style_slug")),
        dimensions_cm=clean_text(w.get("dimensions_cm")),
        dimensions_in=clean_text(w.get("dimensions_in")),
        is_available_for_sale=to_bool(w.get("is_in_stock")),
        image_url=to_url(w.get("image_url")),
        substrate=clean_text(w.get("substrate")),
        materials=clean_text(w.get("materials")),
        signature=clean_text(w.get("signature")),
        has_detail=bool(w.get("has_detail")),
        **stamp,
    )

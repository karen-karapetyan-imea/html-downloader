from collections.abc import Mapping
from datetime import datetime
from functools import partial
from typing import Any

from artworks.mappers._common import Record, artist_names, collect, priced, rows_of
from artworks.schema import ArtworkRow
from etl_core.normalize import clean_text, plain_text, platform_id, sane_year, str_list, to_bool, to_url


def is_relevant(parsed: Mapping[str, Any]) -> bool:
    return parsed.get("page_type") == "artwork"


def to_rows(parsed: Mapping[str, Any], source_file: str, crawled_at: datetime | None) -> list[ArtworkRow]:
    build = partial(_row, names=artist_names(parsed))
    return collect(rows_of(parsed, "artworks"), build, source_file, crawled_at)


def _row(w: Record, stamp: dict[str, Any], *, names: dict[Any, str]) -> ArtworkRow | None:
    artwork_id = platform_id(w.get("artwork_id"))
    if artwork_id is None:
        return None
    price, currency = priced((w.get("price"), w.get("currency")))
    return ArtworkRow(
        platform_artwork_id=artwork_id,
        title=clean_text(w.get("title")),
        artist_name=clean_text(w.get("artist_name")) or names.get(w.get("artist_id")),
        platform_artist_id=platform_id(w.get("artist_id")),
        artwork_url=to_url(w.get("url")),
        category=clean_text(w.get("art_medium")),
        width_in=w.get("width_in") if isinstance(w.get("width_in"), int | float) else None,
        height_in=w.get("height_in") if isinstance(w.get("height_in"), int | float) else None,
        price=price,
        currency=currency,
        year=sane_year(w.get("year")),
        description=plain_text(w.get("description")),
        mediums=str_list(w.get("art_medium")),
        is_available_for_sale=to_bool(w.get("in_stock")),
        is_signed=to_bool(w.get("is_signed")),
        image_url=to_url(w.get("image_url")),
        has_detail=bool(w.get("has_detail")),
        **stamp,
    )

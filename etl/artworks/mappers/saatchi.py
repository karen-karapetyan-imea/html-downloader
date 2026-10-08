from collections import defaultdict
from collections.abc import Mapping
from datetime import datetime
from functools import partial
from typing import Any

from artworks.mappers._common import Record, collect, priced
from artworks.schema import ArtworkRow
from etl_core.normalize import (
    clean_text,
    country_name,
    plain_text,
    platform_id,
    sane_year,
    to_bool,
    to_float,
    to_url,
)

_PAGES = frozenset({"artist_profile", "artwork"})


def is_relevant(parsed: Mapping[str, Any]) -> bool:
    return parsed.get("page_type") in _PAGES and not parsed.get("partial")


def to_rows(parsed: Mapping[str, Any], source_file: str, crawled_at: datetime | None) -> list[ArtworkRow]:
    artist = parsed.get("artist") or {}
    build = partial(_row, artist=artist, tags=_tags(parsed.get("tags")))
    return collect(parsed.get("artworks") or [], build, source_file, crawled_at)


def _tags(tags: list[Record] | None) -> dict[tuple[Any, str], list[str]]:
    """(artwork_id, kind) -> names in page order; kinds are style, medium, material, keyword."""
    grouped: dict[tuple[Any, str], list[str]] = defaultdict(list)
    for tag in tags or []:
        grouped[(tag.get("artwork_id"), tag.get("kind"))].append(tag.get("name"))
    return grouped


def _row(
    w: Record, stamp: dict[str, Any], *, artist: Record, tags: dict[tuple[Any, str], list[str]]
) -> ArtworkRow | None:
    artwork_id = platform_id(w.get("artwork_id"))
    if artwork_id is None:
        return None
    price, currency = priced((w.get("price_usd"), "USD"))
    status = clean_text(w.get("original_status"))
    own_artist = artist.get("artist_id") is not None and artist.get("artist_id") == w.get("artist_id")
    return ArtworkRow(
        platform_artwork_id=artwork_id,
        title=clean_text(w.get("title")),
        artist_name=clean_text(artist.get("full_name")) if own_artist else None,
        platform_artist_id=platform_id(w.get("artist_id")),
        artwork_url=to_url(w.get("url")),
        category=clean_text(w.get("category")),
        width_cm=to_float(w.get("width_cm")),
        height_cm=to_float(w.get("height_cm")),
        depth_cm=to_float(w.get("depth_cm")),
        price=price,
        currency=currency,
        year=sane_year(w.get("year_produced")),
        subject=clean_text(w.get("subject")),
        description=plain_text(w.get("description")),
        styles=tags.get((w.get("artwork_id"), "style")) or None,
        mediums=tags.get((w.get("artwork_id"), "medium")) or None,
        is_available_for_sale=to_bool(w.get("is_available_for_sale")),
        ships_from_country=country_name(w.get("ships_from_country")),
        is_sold=status == "sold" if status else None,
        image_url=to_url(w.get("image_url")),
        has_detail=bool(w.get("has_detail")),
        **stamp,
    )

from collections.abc import Mapping
from datetime import datetime
from functools import partial
from typing import Any

from artworks.mappers._common import Record, collect, priced
from artworks.schema import ArtworkRow
from etl_core.normalize import clean_text, platform_id, str_list, to_bool, to_float, to_url


def is_relevant(parsed: Mapping[str, Any]) -> bool:
    """Artsper is crawled through artist pages only; their artwork cards are the source."""
    return parsed.get("page_type") == "artist"


def to_rows(parsed: Mapping[str, Any], source_file: str, crawled_at: datetime | None) -> list[ArtworkRow]:
    build = partial(_row, artist=parsed.get("artist") or {})
    return collect(parsed.get("artworks") or [], build, source_file, crawled_at)


def _row(w: Record, stamp: dict[str, Any], *, artist: Record) -> ArtworkRow | None:
    artwork_id = platform_id(w.get("artwork_id"))
    if artwork_id is None:
        return None
    artist_id, artist_name = _card_artist(w, artist)
    price, currency = priced((w.get("price_usd"), "USD"), (w.get("price_eur"), "EUR"))
    return ArtworkRow(
        platform_artwork_id=artwork_id,
        title=clean_text(w.get("title")),
        artist_name=artist_name,
        platform_artist_id=artist_id,
        artwork_url=to_url(w.get("url")),
        category=clean_text(w.get("category")),
        width_cm=to_float(w.get("width_cm")),
        height_cm=to_float(w.get("height_cm")),
        depth_cm=to_float(w.get("depth_cm")),
        price=price,
        currency=currency,
        mediums=str_list(w.get("medium_label")),
        dimensions_cm=clean_text(w.get("dimensions_cm")),
        dimensions_in=clean_text(w.get("dimensions_in")),
        width_in=to_float(w.get("width_in")),
        height_in=to_float(w.get("height_in")),
        depth_in=to_float(w.get("depth_in")),
        is_sold=to_bool(w.get("is_sold")),
        is_price_on_request=to_bool(w.get("is_price_on_request")),
        image_url=to_url(w.get("image_url")),
        has_detail=False,
        **stamp,
    )


def _card_artist(card: Record, page_artist: Record) -> tuple[str | None, str | None]:
    """Artist pages also show other artists' works; the card's own artist wins over the page's."""
    page_id = platform_id(page_artist.get("artist_id"))
    card_id = platform_id(card.get("artist_id")) or page_id
    if card_id == page_id:
        return page_id, clean_text(page_artist.get("name"))
    return card_id, clean_text(card.get("artist_name"))

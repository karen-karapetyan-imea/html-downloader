from collections.abc import Mapping
from datetime import datetime
from typing import Any

from artists.mappers._common import own_artist_row
from artists.schema import ArtistRow
from etl_core.normalize import clean_text, normalize_gender, plain_text, platform_id, sane_year, to_url


def is_artist_page(parsed: Mapping[str, Any]) -> bool:
    return parsed.get("page_type") == "artist"


def to_artist(parsed: Mapping[str, Any], source_file: str, crawled_at: datetime | None) -> ArtistRow | None:
    a = own_artist_row(parsed)
    artist_id = platform_id(a.get("artist_id")) if a else None
    if a is None or artist_id is None:
        return None
    return ArtistRow(
        platform_artist_id=artist_id,
        full_name=clean_text(a.get("name")),
        profile_url=to_url(a.get("url")),
        avatar=to_url(a.get("og_image_url")) or to_url(a.get("cover_image_url")),
        birth_year=sane_year(a.get("birth_year")),
        death_year=sane_year(a.get("death_year")),
        gender=normalize_gender(a.get("gender")),
        biography=plain_text(a.get("biography")) or plain_text(a.get("biography_html")),
        nationality=clean_text(a.get("nationality")),
        source_file=source_file,
        crawled_at=crawled_at,
    )

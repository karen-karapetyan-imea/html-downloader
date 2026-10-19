from collections.abc import Mapping
from datetime import datetime
from typing import Any

from artists.mappers._common import own_artist_row
from artists.schema import ArtistRow
from etl_core.normalize import clean_text, country_name, plain_text, platform_id, to_url


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
        avatar=to_url(a.get("avatar_url")),
        biography=plain_text(a.get("biography")),
        city=clean_text(a.get("city")),
        state=clean_text(a.get("region")),
        country=country_name(a.get("country")),
        source_file=source_file,
        crawled_at=crawled_at,
    )

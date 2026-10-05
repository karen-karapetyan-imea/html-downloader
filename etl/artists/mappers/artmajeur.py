from collections.abc import Mapping
from datetime import datetime
from typing import Any

from artists.mappers._common import as_list, own_artist_row, platform_id
from artists.normalize import (
    classify_social_links,
    clean_text,
    country_name,
    normalize_gender,
    plain_text,
    sane_year,
    to_url,
)
from artists.schema import ArtistRow


def is_artist_page(parsed: Mapping[str, Any]) -> bool:
    return parsed.get("page_type") == "artist"


def to_artist(parsed: Mapping[str, Any], source_file: str, crawled_at: datetime | None) -> ArtistRow | None:
    a = own_artist_row(parsed)
    artist_id = platform_id(a.get("artist_id")) if a else None
    if a is None or artist_id is None:
        return None
    links = classify_social_links(as_list(a.get("social_links")))
    return ArtistRow(
        platform_artist_id=artist_id,
        full_name=clean_text(a.get("name")),
        profile_url=to_url(a.get("url")),
        avatar=to_url(a.get("portrait_url")),
        birth_year=sane_year(a.get("birth_year")),
        gender=normalize_gender(a.get("gender")),
        biography=plain_text(a.get("biography")),
        website=links.website,
        facebook=links.facebook,
        instagram=links.instagram,
        tiktok=links.tiktok,
        twitter=links.twitter,
        nationality=clean_text(a.get("nationality")),
        city=clean_text(a.get("city")),
        state=clean_text(a.get("region")),
        zip_code=clean_text(a.get("postal_code")),
        country=country_name(a.get("country_code")),
        source_file=source_file,
        crawled_at=crawled_at,
    )

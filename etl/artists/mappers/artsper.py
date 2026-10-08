from collections.abc import Mapping
from datetime import datetime
from typing import Any

from artists.mappers._common import image_url
from artists.schema import ArtistRow
from etl_core.normalize import (
    as_list,
    classify_social_links,
    clean_text,
    plain_text,
    platform_id,
    sane_year,
    to_url,
)


def is_artist_page(parsed: Mapping[str, Any]) -> bool:
    return parsed.get("page_type") == "artist"


def to_artist(parsed: Mapping[str, Any], source_file: str, crawled_at: datetime | None) -> ArtistRow | None:
    a = parsed.get("artist") or {}
    artist_id = platform_id(a.get("artist_id"))
    if artist_id is None:
        return None
    links = classify_social_links(as_list(a.get("same_as")))
    return ArtistRow(
        platform_artist_id=artist_id,
        full_name=clean_text(a.get("name")),
        profile_url=to_url(a.get("url")),
        avatar=to_url(image_url(a.get("image_url"))),
        birth_year=sane_year(a.get("birth_year")),
        death_year=sane_year(a.get("death_year")),
        biography=plain_text(a.get("biography_text")) or plain_text(a.get("description")),
        website=links.website,
        facebook=links.facebook,
        instagram=links.instagram,
        tiktok=links.tiktok,
        twitter=links.twitter,
        nationality=clean_text(a.get("nationality_label")),
        source_file=source_file,
        crawled_at=crawled_at,
    )

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from artists.mappers._common import own_artist_row, platform_id
from artists.normalize import (
    Links,
    classify_social_links,
    clean_text,
    country_name,
    plain_text,
    to_url,
    username_to_url,
)
from artists.schema import ArtistRow


def is_artist_page(parsed: Mapping[str, Any]) -> bool:
    return parsed.get("page_type") == "artist"


def to_artist(parsed: Mapping[str, Any], source_file: str, crawled_at: datetime | None) -> ArtistRow | None:
    a = own_artist_row(parsed)
    artist_id = platform_id(a.get("artist_id")) if a else None
    if a is None or artist_id is None:
        return None
    own_links = Links(
        website=to_url(a.get("website_url")),
        facebook=username_to_url("facebook", a.get("facebook_url")),
        instagram=username_to_url("instagram", a.get("instagram_username")),
        twitter=username_to_url("twitter", a.get("twitter_username")),
    )
    social_rows = (parsed.get("rows") or {}).get("artist_social_links") or []
    links = own_links.fill_from(
        classify_social_links(s.get("url") for s in social_rows if s.get("artist_id") == a.get("artist_id"))
    )
    return ArtistRow(
        platform_artist_id=artist_id,
        full_name=clean_text(a.get("name")),
        profile_url=to_url(a.get("url")),
        avatar=to_url(a.get("avatar_url")),
        biography=plain_text(a.get("biography")) or plain_text(a.get("intro")),
        website=links.website,
        facebook=links.facebook,
        instagram=links.instagram,
        tiktok=links.tiktok,
        twitter=links.twitter,
        country=country_name(a.get("country")) or country_name(a.get("country_code")),
        source_file=source_file,
        crawled_at=crawled_at,
    )

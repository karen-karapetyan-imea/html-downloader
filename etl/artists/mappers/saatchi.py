from collections.abc import Mapping
from datetime import datetime
from typing import Any

from artists.mappers._common import platform_id
from artists.normalize import clean_text, country_name, plain_text, to_url, username_to_url
from artists.schema import ArtistRow


def is_artist_page(parsed: Mapping[str, Any]) -> bool:
    return parsed.get("page_type") == "artist_profile" and not parsed.get("partial")


def to_artist(parsed: Mapping[str, Any], source_file: str, crawled_at: datetime | None) -> ArtistRow | None:
    a = parsed.get("artist") or {}
    artist_id = platform_id(a.get("artist_id"))
    if artist_id is None:
        return None
    return ArtistRow(
        platform_artist_id=artist_id,
        full_name=clean_text(a.get("full_name")),
        profile_url=to_url(a.get("profile_url")),
        avatar=to_url(a.get("avatar_url")),
        biography=plain_text(a.get("about")),
        website=to_url(a.get("website_url")),
        facebook=username_to_url("facebook", a.get("facebook_url")),
        instagram=username_to_url("instagram", a.get("instagram_url")),
        tiktok=username_to_url("tiktok", a.get("tiktok_url")),
        twitter=username_to_url("twitter", a.get("twitter_url")),
        city=clean_text(a.get("city")),
        state=clean_text(a.get("state")),
        zip_code=clean_text(a.get("zipcode")),
        country=country_name(a.get("country")) or country_name(a.get("country_code")),
        source_file=source_file,
        crawled_at=crawled_at,
    )

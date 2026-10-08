"""The final artist record and its fixed Parquet schema."""

from dataclasses import dataclass
from datetime import datetime

import pyarrow as pa


@dataclass(frozen=True, slots=True)
class ArtistRow:
    platform_artist_id: str
    full_name: str | None = None
    profile_url: str | None = None
    avatar: str | None = None
    birth_year: int | None = None
    death_year: int | None = None
    gender: str | None = None
    biography: str | None = None
    website: str | None = None
    facebook: str | None = None
    instagram: str | None = None
    tiktok: str | None = None
    twitter: str | None = None
    nationality: str | None = None
    city: str | None = None
    state: str | None = None
    zip_code: str | None = None
    country: str | None = None
    source_file: str | None = None
    crawled_at: datetime | None = None


ARTIST_SCHEMA = pa.schema(
    [
        pa.field("platform_artist_id", pa.string(), nullable=False),
        pa.field("full_name", pa.string()),
        pa.field("profile_url", pa.string()),
        pa.field("avatar", pa.string()),
        pa.field("birth_year", pa.int16()),
        pa.field("death_year", pa.int16()),
        pa.field("gender", pa.string()),
        pa.field("biography", pa.string()),
        pa.field("website", pa.string()),
        pa.field("facebook", pa.string()),
        pa.field("instagram", pa.string()),
        pa.field("tiktok", pa.string()),
        pa.field("twitter", pa.string()),
        pa.field("nationality", pa.string()),
        pa.field("city", pa.string()),
        pa.field("state", pa.string()),
        pa.field("zip_code", pa.string()),
        pa.field("country", pa.string()),
        pa.field("source_file", pa.string()),
        pa.field("crawled_at", pa.timestamp("us", tz="UTC")),
    ]
)

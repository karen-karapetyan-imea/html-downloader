"""One auction lot and its fixed Parquet schema.

`platform_lot_id` is the house's own stable id (the same id the downloader dedupes on).
Money is the source currency only. A missing price and a literal 0 both stay null: 0 is not a hammer.
"""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

import pyarrow as pa

from artworks.schema import PRICE_TYPE


@dataclass(frozen=True, slots=True)
class LotRow:
    platform_lot_id: str
    lot_url: str | None = None
    lot_number: str | None = None
    title: str | None = None
    description: str | None = None
    artist_name: str | None = None
    category: str | None = None
    image_url: str | None = None
    currency: str | None = None
    low_estimate: Decimal | None = None
    high_estimate: Decimal | None = None
    price_realized: Decimal | None = None
    sale_status: str | None = None
    sale_id: str | None = None
    sale_title: str | None = None
    sale_date: date | None = None
    sale_url: str | None = None
    selling_house_name: str | None = None
    is_result: bool | None = None  # realised-price page, so it beats the live twin of the same id
    has_detail: bool | None = None
    # the work and its artist, named like the artworks/artists datasets; structured fields where
    # the house has them, else read from the catalogue text (lots/catalogue.py)
    height_cm: float | None = None
    width_cm: float | None = None
    depth_cm: float | None = None
    medium: str | None = None
    year: int | None = None
    is_signed: bool | None = None
    artist_birth_year: int | None = None
    artist_death_year: int | None = None
    artist_nationality: str | None = None
    sale_location: str | None = None
    department: str | None = None
    source_file: str | None = None
    crawled_at: datetime | None = None


LOT_SCHEMA = pa.schema(
    [
        pa.field("platform_lot_id", pa.string(), nullable=False),
        pa.field("lot_url", pa.string()),
        pa.field("lot_number", pa.string()),
        pa.field("title", pa.string()),
        pa.field("description", pa.string()),
        pa.field("artist_name", pa.string()),
        pa.field("category", pa.string()),
        pa.field("image_url", pa.string()),
        pa.field("currency", pa.string()),
        pa.field("low_estimate", PRICE_TYPE),
        pa.field("high_estimate", PRICE_TYPE),
        pa.field("price_realized", PRICE_TYPE),
        pa.field("sale_status", pa.string()),
        pa.field("sale_id", pa.string()),
        pa.field("sale_title", pa.string()),
        pa.field("sale_date", pa.date32()),
        pa.field("sale_url", pa.string()),
        pa.field("selling_house_name", pa.string()),
        pa.field("is_result", pa.bool_()),
        pa.field("has_detail", pa.bool_()),
        pa.field("height_cm", pa.float64()),
        pa.field("width_cm", pa.float64()),
        pa.field("depth_cm", pa.float64()),
        pa.field("medium", pa.string()),
        pa.field("year", pa.int16()),
        pa.field("is_signed", pa.bool_()),
        pa.field("artist_birth_year", pa.int16()),
        pa.field("artist_death_year", pa.int16()),
        pa.field("artist_nationality", pa.string()),
        pa.field("sale_location", pa.string()),
        pa.field("department", pa.string()),
        pa.field("source_file", pa.string()),
        pa.field("crawled_at", pa.timestamp("us", tz="UTC")),
    ]
)

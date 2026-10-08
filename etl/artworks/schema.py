"""The final artwork record and its fixed Parquet schema.

Column order follows etl/artwork_structure.csv: first the fields every marketplace has, then
the rest by how many marketplaces have them, then technical columns. Only the dedup key is
required; every other column is nullable.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

import pyarrow as pa

PRICE_TYPE = pa.decimal128(14, 2)


@dataclass(frozen=True, slots=True)
class ArtworkRow:
    platform_artwork_id: str
    # all 5 marketplaces
    title: str | None = None
    artist_name: str | None = None
    platform_artist_id: str | None = None
    artwork_url: str | None = None
    category: str | None = None
    width_cm: float | None = None
    height_cm: float | None = None
    depth_cm: float | None = None
    price: Decimal | None = None
    currency: str | None = None
    # 4 of 5
    year: int | None = None
    subject: str | None = None
    description: str | None = None
    styles: list[str] | None = None
    mediums: list[str] | None = None
    # 2 of 5
    dimensions_cm: str | None = None
    dimensions_in: str | None = None
    width_in: float | None = None
    height_in: float | None = None
    depth_in: float | None = None
    is_available_for_sale: bool | None = None
    ships_from_country: str | None = None
    # 1 of 5
    dimensions: str | None = None
    is_sold: bool | None = None
    is_price_on_request: bool | None = None
    image_url: str | None = None
    technique: str | None = None
    support: str | None = None
    is_ai_generated: bool | None = None
    has_certificate: bool | None = None
    is_signed: bool | None = None
    substrate: str | None = None
    materials: str | None = None
    signature: str | None = None
    # technical
    has_detail: bool | None = None  # row comes from the artwork's own page, not a card on another page
    source_file: str | None = None
    crawled_at: datetime | None = None


ARTWORK_SCHEMA = pa.schema(
    [
        pa.field("platform_artwork_id", pa.string(), nullable=False),
        pa.field("title", pa.string()),
        pa.field("artist_name", pa.string()),
        pa.field("platform_artist_id", pa.string()),
        pa.field("artwork_url", pa.string()),
        pa.field("category", pa.string()),
        pa.field("width_cm", pa.float64()),
        pa.field("height_cm", pa.float64()),
        pa.field("depth_cm", pa.float64()),
        pa.field("price", PRICE_TYPE),
        pa.field("currency", pa.string()),
        pa.field("year", pa.int16()),
        pa.field("subject", pa.string()),
        pa.field("description", pa.string()),
        pa.field("styles", pa.list_(pa.string())),
        pa.field("mediums", pa.list_(pa.string())),
        pa.field("dimensions_cm", pa.string()),
        pa.field("dimensions_in", pa.string()),
        pa.field("width_in", pa.float64()),
        pa.field("height_in", pa.float64()),
        pa.field("depth_in", pa.float64()),
        pa.field("is_available_for_sale", pa.bool_()),
        pa.field("ships_from_country", pa.string()),
        pa.field("dimensions", pa.string()),
        pa.field("is_sold", pa.bool_()),
        pa.field("is_price_on_request", pa.bool_()),
        pa.field("image_url", pa.string()),
        pa.field("technique", pa.string()),
        pa.field("support", pa.string()),
        pa.field("is_ai_generated", pa.bool_()),
        pa.field("has_certificate", pa.bool_()),
        pa.field("is_signed", pa.bool_()),
        pa.field("substrate", pa.string()),
        pa.field("materials", pa.string()),
        pa.field("signature", pa.string()),
        pa.field("has_detail", pa.bool_()),
        pa.field("source_file", pa.string()),
        pa.field("crawled_at", pa.timestamp("us", tz="UTC")),
    ]
)

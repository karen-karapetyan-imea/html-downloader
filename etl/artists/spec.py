"""The artists dataset: one row per artist page, latest crawl wins."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from artists.mappers import ArtistMapper, get_mapper
from artists.schema import ARTIST_SCHEMA, ArtistRow
from etl_core.dataset import DatasetSpec
from etl_core.schema import CRAWL_DATE_COLUMN
from etl_core.sources import PLATFORMS


@dataclass(frozen=True, slots=True)
class _OneRowPerPage:
    """Adapts an artist mapper (one row or None per page) to the dataset Mapper protocol."""

    mapper: ArtistMapper

    def is_relevant(self, parsed: Mapping[str, Any]) -> bool:
        return self.mapper.is_artist_page(parsed)

    def to_rows(
        self, parsed: Mapping[str, Any], source_file: str, crawled_at: datetime | None
    ) -> list[ArtistRow]:
        row = self.mapper.to_artist(parsed, source_file, crawled_at)
        return [] if row is None else [row]


ARTISTS = DatasetSpec[ArtistRow](
    name="artists",
    prog="artist-etl",
    ref="artists.spec:ARTISTS",
    row_type=ArtistRow,
    schema=ARTIST_SCHEMA,
    key_column="platform_artist_id",
    mappers={p: _OneRowPerPage(get_mapper(p)) for p in PLATFORMS},
    snapshot_order="crawled_at DESC NULLS LAST, source_file DESC",
    current_order=f"crawled_at DESC NULLS LAST, {CRAWL_DATE_COLUMN} DESC, source_file DESC",
    default_out="output/artists",
)

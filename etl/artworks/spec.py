"""The artworks dataset: one row per artwork, from artwork pages and from cards on other pages.

Cards are partial, so inside one crawl the artwork's own page wins. Across crawls the newest
crawl wins first: its price and sale status are current, even if it only has a card.
"""

from artworks.mappers import MAPPERS
from artworks.schema import ARTWORK_SCHEMA, ArtworkRow
from etl_core.dataset import DatasetSpec
from etl_core.schema import CRAWL_DATE_COLUMN

_WITHIN_CRAWL = "has_detail DESC NULLS LAST, crawled_at DESC NULLS LAST, source_file DESC"

ARTWORKS = DatasetSpec[ArtworkRow](
    name="artworks",
    prog="artwork-etl",
    ref="artworks.spec:ARTWORKS",
    row_type=ArtworkRow,
    schema=ARTWORK_SCHEMA,
    key_column="platform_artwork_id",
    mappers=MAPPERS,
    snapshot_order=_WITHIN_CRAWL,
    current_order=f"{CRAWL_DATE_COLUMN} DESC, {_WITHIN_CRAWL}",
    default_out="output/artworks",
)

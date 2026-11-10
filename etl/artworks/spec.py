"""The artworks dataset: one row per artwork, from artwork pages and from cards on other pages.

Cards are partial, so inside one crawl the artwork's own page wins. Across crawls the newest
crawl date wins first: its price and sale status are current, even if it only has a card.
"""

from artworks.mappers import MAPPERS
from artworks.schema import ARTWORK_SCHEMA, ArtworkRow
from etl_core.dataset import DatasetSpec
from etl_core.documents import SortField

ARTWORKS = DatasetSpec[ArtworkRow](
    name="artworks",
    prog="artwork-etl",
    ref="artworks.spec:ARTWORKS",
    row_type=ArtworkRow,
    schema=ARTWORK_SCHEMA,
    key_column="platform_artwork_id",
    mappers=MAPPERS,
    within_crawl_order=(
        SortField("has_detail"),
        SortField("crawled_at"),
        SortField("source_file"),
    ),
    default_out="output/artworks",
    private_sales_dataset="artworks",
)

"""The lots dataset: one row per auction lot. Newest crawl date wins, then a realised page."""

from etl_core.dataset import DatasetSpec
from etl_core.documents import SortField
from lots.mappers import MAPPERS
from lots.schema import LOT_SCHEMA, LotRow

# Realised twin beats the live page of the same id; a detail page beats anything else.
_WITHIN_CRAWL = (
    "is_result DESC NULLS LAST, has_detail DESC NULLS LAST, crawled_at DESC NULLS LAST, source_file DESC"
)

LOTS = DatasetSpec[LotRow](
    name="lots",
    prog="lot-etl",
    ref="lots.spec:LOTS",
    row_type=LotRow,
    schema=LOT_SCHEMA,
    key_column="platform_lot_id",
    mappers=MAPPERS,
    within_crawl_order=(
        SortField("is_result"),
        SortField("has_detail"),
        SortField("crawled_at"),
        SortField("source_file"),
    ),
    default_out="output/lots",
)

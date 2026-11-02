"""Unit tests for identity helpers and within-crawl winner ordering (no MongoDB)."""

from datetime import UTC, datetime
from decimal import Decimal

from artists.schema import ArtistRow
from artworks.schema import ArtworkRow
from etl_core.documents import (
    SortField,
    crawl_date_str,
    dedup_rows,
    encode_value,
    entity_id,
    version_id,
    winner_sort_key,
)
from lots.schema import LotRow


def test_identity_helpers():
    assert entity_id("artsper", "12345") == "artsper:12345"
    assert version_id("artsper:12345", "2026-09-21") == "artsper:12345:2026-09-21"
    assert crawl_date_str("2026-10-01") == "2026-10-01"


def test_decimal_and_datetime_encoding():
    assert str(encode_value(Decimal("12.50"))) == "12.50"
    dt = datetime(2026, 9, 21, 10, 0, 0)
    assert encode_value(dt).tzinfo == UTC


def test_artist_newer_crawled_at_wins():
    order = (SortField("crawled_at"), SortField("source_file"))
    older = ArtistRow("1", full_name="A", source_file="a.html", crawled_at=datetime(2026, 1, 1, tzinfo=UTC))
    newer = ArtistRow("1", full_name="B", source_file="b.html", crawled_at=datetime(2026, 2, 1, tzinfo=UTC))
    assert dedup_rows([older, newer], "platform_artist_id", order)[0].full_name == "B"
    assert winner_sort_key(newer, order) < winner_sort_key(older, order)


def test_artwork_detail_beats_card():
    order = (SortField("has_detail"), SortField("crawled_at"), SortField("source_file"))
    card = ArtworkRow("5", title="card", has_detail=False, source_file="a.html")
    detail = ArtworkRow("5", title="detail", has_detail=True, source_file="b.html")
    assert dedup_rows([card, detail], "platform_artwork_id", order)[0].title == "detail"


def test_lot_result_beats_live():
    order = (
        SortField("is_result"),
        SortField("has_detail"),
        SortField("crawled_at"),
        SortField("source_file"),
    )
    live = LotRow("1", title="live", is_result=False, has_detail=True, source_file="live.html")
    sold = LotRow("1", title="sold", is_result=True, has_detail=True, source_file="sold.html")
    assert dedup_rows([live, sold], "platform_lot_id", order)[0].title == "sold"

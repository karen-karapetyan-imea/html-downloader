"""Auction lot parsers: one trimmed page per house, plus monthly crawl folders."""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pyarrow.parquet as pq

from etl_core.compact import compact
from etl_core.crawl import detect_crawl_date, parse_folder_date
from etl_core.layout import current_dir
from etl_core.pipeline import RunConfig, run_snapshot
from etl_core.sources import AUCTION_PLATFORMS, parse_html
from etl_core.sync import find_pending
from lots.mappers import MAPPERS
from lots.spec import LOTS
from tests.builders import make_crawl, write_crawl_manifest

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "auctions"
CRAWLED_AT = None


def row(platform: str, name: str, url: str | None = None):
    parsed = parse_html(platform, (FIXTURES / name).read_text(encoding="utf-8"), url)
    mapper = MAPPERS[platform]
    assert mapper.is_relevant(parsed), platform
    rows = mapper.to_rows(parsed, name, CRAWLED_AT)
    assert len(rows) == 1
    return rows[0]


def test_artcurial_sold_lot():
    lot = row("artcurial", "artcurial.html")
    assert lot.platform_lot_id == "IT6607:1-a"
    assert lot.lot_number == "1a"
    assert lot.title == "HERMÈS L'ETRIER"
    assert lot.description == "Made of porcelain"
    assert lot.low_estimate == Decimal("1000.00")
    assert lot.high_estimate == Decimal("1500.00")
    assert lot.price_realized == Decimal("2800.00")
    assert lot.currency == "EUR"
    assert lot.sale_id == "IT6607"
    assert lot.sale_title == "Luxury Lifestyle Online"
    assert lot.sale_date == date(2026, 10, 22)
    assert lot.sale_status == "sold"
    assert lot.is_result is True


def test_barnebys_live_and_realised_share_the_numeric_id():
    live = row("barnebys", "barnebys_live.html")
    realised = row(
        "barnebys",
        "barnebys_realised.html",
        "https://www.barnebys.com/realized-prices/lot/622510218/landscape",
    )
    assert live.platform_lot_id == realised.platform_lot_id == "622510218"
    assert live.low_estimate == Decimal("285.06")
    assert live.high_estimate is None
    assert live.price_realized is None
    assert live.sale_status == "upcoming"
    assert live.is_result is False
    assert live.selling_house_name == "Aurora & Athena"
    assert realised.price_realized == Decimal("400.00")
    assert realised.sale_status == "sold"
    assert realised.is_result is True


def test_christies_price_realised():
    lot = row("christies", "christies.html")
    assert lot.platform_lot_id == "6470000"
    assert lot.lot_number == "203"
    assert lot.artist_name == "MARC CHAGALL (1887-1985)"
    assert lot.title == "Étude pour le paysage rouge"
    assert lot.currency == "GBP"
    assert lot.low_estimate == Decimal("8000.00")
    assert lot.high_estimate == Decimal("12000.00")
    assert lot.price_realized == Decimal("44100.00")
    assert lot.sale_id == "22658"
    assert lot.sale_date == date(2024, 3, 8)
    assert lot.sale_status == "sold"
    assert lot.selling_house_name is None


def test_drouot_zero_result_is_not_a_hammer():
    lot = row("drouot", "drouot.html")
    assert lot.platform_lot_id == "34624465"
    assert lot.lot_number == "18"
    assert lot.title == "TIFFANY & CO. necklace"
    assert lot.description is not None and "silver chain" in lot.description
    assert lot.low_estimate == Decimal("500.00")
    assert lot.high_estimate == Decimal("800.00")
    assert lot.price_realized is None
    assert lot.currency == "EUR"
    assert lot.sale_id == "184089"
    assert lot.sale_title == "AUCTION 395"
    assert lot.sale_status == "upcoming"
    assert lot.category == "Necklaces"
    assert lot.selling_house_name == "Bertolami Fine Art s.r.l."
    assert lot.image_url == "https://cdn.drouot.com/d/lot/x.jpg"


def test_invaluable_open_lot_keeps_a_zero_sold_amount_empty():
    lot = row("invaluable", "invaluable.html")
    assert lot.platform_lot_id == "a9a6bd0a9c"
    assert lot.artist_name == "Alex Ukrainets"
    assert lot.low_estimate == Decimal("320.00")
    assert lot.high_estimate == Decimal("400.00")
    assert lot.price_realized is None
    assert lot.currency == "USD"
    assert lot.sale_status == "upcoming"
    assert lot.selling_house_name == "Broward Auction Gallery LLC"
    assert lot.sale_id == "ABC123"
    assert lot.sale_url == "https://www.invaluable.com/catalog/ABC123"


def test_liveauctioneers_price_result():
    lot = row(
        "liveauctioneers",
        "liveauctioneers.html",
        "https://www.liveauctioneers.com/price-result/-0-0-da-mosa-bianchi-benedizione-olio-su-tela/",
    )
    assert lot.platform_lot_id == "-0-0-da-mosa-bianchi-benedizione-olio-su-tela"
    assert lot.lot_number == "2425"
    assert lot.price_realized == Decimal("2000.00")
    assert lot.low_estimate == Decimal("2200.00")
    assert lot.high_estimate == Decimal("2500.00")
    assert lot.currency == "EUR"
    assert lot.sale_status == "sold"
    assert lot.selling_house_name == "Il Ponte Casa d'Aste Srl"
    assert lot.sale_id == "103239"
    assert lot.is_result is True


def test_saleroom_estimate_is_not_the_opening_bid():
    lot = row("saleroom", "saleroom.html")
    assert lot.platform_lot_id == "8673c605-2715-425f-b3a7-b4e500fb4006"
    assert lot.title == "Paintings"
    assert lot.description == "Igor Tcholaria (Georgian, b.1959) Woman wearing a hat"
    assert lot.low_estimate == Decimal("100.00")
    assert lot.high_estimate == Decimal("150.00")
    assert lot.price_realized is None
    assert lot.currency == "GBP"
    assert lot.sale_status == "upcoming"
    assert lot.sale_date == date(2026, 10, 24)
    assert lot.selling_house_name == "Henry Aldridge & Son Limited"
    assert lot.sale_id == "henry-10079"
    assert lot.sale_url is not None and lot.sale_url.endswith("catalogue-id-henry-10079")
    assert lot.is_result is False


def test_sothebys_final_price():
    lot = row("sothebys", "sothebys.html")
    assert lot.platform_lot_id == "2025/arcade-new-york/untitled-pasta-painting"
    assert lot.lot_number == "1"
    assert lot.artist_name == "Scott Reeder"
    assert lot.title == "Untitled (Pasta Painting)"
    assert lot.low_estimate == Decimal("2000.00")
    assert lot.high_estimate == Decimal("3000.00")
    assert lot.price_realized == Decimal("3302.00")
    assert lot.currency == "USD"
    assert lot.sale_id == "N11862"
    assert lot.sale_date == date(2025, 3, 26)
    assert lot.sale_status == "sold"
    assert lot.lot_url == "https://www.sothebys.com/en/buy/auction/2025/arcade-new-york/untitled-pasta-painting"


def test_challenge_page_yields_no_row():
    html = (FIXTURES / "challenge.html").read_text(encoding="utf-8")
    for platform in AUCTION_PLATFORMS:
        parsed = parse_html(platform, html, "https://example.test/lot")
        assert not MAPPERS[platform].is_relevant(parsed)
        assert MAPPERS[platform].to_rows(parsed, "challenge.html", None) == []


def test_month_folder_is_the_first_of_the_month(tmp_path):
    assert parse_folder_date("2026-10") == date(2026, 10, 1)
    assert parse_folder_date("2026-13") is None
    assert parse_folder_date("2026-09-23") == date(2026, 9, 23)
    month = tmp_path / "2026-10"
    month.mkdir()
    assert detect_crawl_date(month) == date(2026, 10, 1)


def test_realised_barnebys_page_wins_inside_the_month(tmp_path):
    data = make_crawl(
        tmp_path / "barnebys",
        "2026-10",
        {
            "live.html": (FIXTURES / "barnebys_live.html").read_text(encoding="utf-8"),
            "sold.html": (FIXTURES / "barnebys_realised.html").read_text(encoding="utf-8"),
        },
        log=[
            {
                "filename": "sold.html",
                "url": "https://www.barnebys.com/realized-prices/lot/622510218/landscape",
                "timestamp": "2026-10-02T00:00:00Z",
            }
        ],
    )
    write_crawl_manifest(data, finished_at="2026-10-02T00:00:00Z")
    out = tmp_path / "out"
    assert find_pending(LOTS, tmp_path, out)[0].crawl_date == date(2026, 10, 1)

    run_snapshot(RunConfig(LOTS, "barnebys", data, out, date(2026, 10, 1), workers=1, chunk_size=10))
    compact(LOTS, "barnebys", out)
    table = pq.read_table(current_dir(out, "barnebys") / "part-00000.parquet")
    assert table.num_rows == 1
    assert table.column("platform_lot_id")[0].as_py() == "622510218"
    assert table.column("price_realized")[0].as_py() == Decimal("400.00")

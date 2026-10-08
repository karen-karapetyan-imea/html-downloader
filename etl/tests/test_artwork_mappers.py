from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from artworks.schema import ARTWORK_SCHEMA, ArtworkRow
from artworks.spec import ARTWORKS
from etl_core.schema import to_table
from etl_core.sources import parse_html
from tests import builders

CRAWLED_AT = datetime(2026, 9, 23, tzinfo=UTC)
SAMPLES_DIR = Path(__file__).resolve().parent.parent / "samples"
COMMON_TO_ALL_MARKETPLACES = (
    "platform_artwork_id",
    "title",
    "artist_name",
    "platform_artist_id",
    "artwork_url",
    "category",
    "width_cm",
    "height_cm",
    "depth_cm",
    "price",
    "currency",
)


def map_page(platform: str, html: str) -> list[ArtworkRow]:
    parsed = parse_html(platform, html)
    mapper = ARTWORKS.mapper(platform)
    assert mapper.is_relevant(parsed), f"{platform} page not detected as an artwork source"
    return mapper.to_rows(parsed, "page.html", CRAWLED_AT)


def test_schema_puts_fields_common_to_all_marketplaces_first():
    assert tuple(ARTWORK_SCHEMA.names[: len(COMMON_TO_ALL_MARKETPLACES)]) == COMMON_TO_ALL_MARKETPLACES
    assert ARTWORK_SCHEMA.names[-3:] == ["has_detail", "source_file", "crawled_at"]


def test_only_the_key_is_required():
    required = [f.name for f in ARTWORK_SCHEMA if not f.nullable]
    assert required == ["platform_artwork_id"]


def test_saatchi_artwork_page():
    assert map_page("saatchi", builders.saatchi_artwork()) == [
        ArtworkRow(
            platform_artwork_id="5",
            title="Blue",
            artist_name="Jane Doe",
            platform_artist_id="123",
            artwork_url="https://www.saatchiart.com/art/Painting-Blue/123/1005/view",
            category="Painting",
            width_cm=50.0,
            height_cm=70.0,
            depth_cm=2.0,
            price=Decimal("1500.00"),
            currency="USD",
            year=2021,
            subject="Abstract",
            description="Blue study.",
            styles=["Abstract"],
            mediums=["Oil"],
            is_available_for_sale=True,
            ships_from_country="Germany",
            is_sold=False,
            image_url="https://images.saatchiart.com/blue.jpg",
            has_detail=True,
            source_file="page.html",
            crawled_at=CRAWLED_AT,
        )
    ]


def test_saatchi_profile_cards():
    rows = map_page(
        "saatchi", builders.saatchi_artist(artworks=[builders.saatchi_card(5), builders.saatchi_card(6)])
    )
    assert [r.platform_artwork_id for r in rows] == ["5", "6"]
    assert {r.artist_name for r in rows} == {"Jane Doe"}
    assert rows[0].mediums == ["Oil", "Acrylic"]
    assert rows[0].price == Decimal("1000.00")
    assert not rows[0].has_detail


def test_artsy_artwork_page():
    assert map_page("artsy", builders.artsy_artwork()) == [
        ArtworkRow(
            platform_artwork_id="andy-warhol-flowers",
            title="Flowers",
            artist_name="Andy Warhol",
            platform_artist_id="4d8b92b34eb68a1b2c0003f4",
            artwork_url="https://www.artsy.net/artwork/andy-warhol-flowers",
            category="Print",
            width_cm=91.4,
            height_cm=91.4,
            price=Decimal("25000.00"),
            currency="USD",
            year=1970,
            description="Bright flowers.",
            mediums=["Screenprint on paper"],
            dimensions_cm="91.4 x 91.4 cm",
            dimensions_in="36 x 36 in",
            is_available_for_sale=True,
            ships_from_country="United States",
            is_sold=False,
            image_url="https://d32dm0rphc51dk.cloudfront.net/flowers.jpg",
            has_certificate=True,
            signature="Hand-signed by artist",
            has_detail=True,
            source_file="page.html",
            crawled_at=CRAWLED_AT,
        )
    ]


def test_artsy_artist_page_notable_works_use_the_slug_as_id():
    notable = [{"href": "/artwork/andy-warhol-marilyn", "title": "Marilyn", "date": "1967"}]
    (row,) = map_page("artsy", builders.artsy_artist(notable=notable))
    assert (row.platform_artwork_id, row.artist_name, row.year) == (
        "andy-warhol-marilyn",
        "Andy Warhol",
        1967,
    )
    assert not row.has_detail


def test_artfinder_artwork_page_prefers_the_sellers_currency():
    assert map_page("artfinder", builders.artfinder_artwork()) == [
        ArtworkRow(
            platform_artwork_id="9001",
            title="Sunset",
            artist_name="John Smith",
            platform_artist_id="42",
            artwork_url="https://www.artfinder.com/product/sunset/",
            category="Oil painting",
            width_cm=50.0,
            height_cm=40.0,
            depth_cm=2.0,
            price=Decimal("450.00"),
            currency="GBP",
            year=2022,
            subject="Landscape",
            description="Warm evening.",
            styles=["Impressionistic"],
            dimensions_cm="50 x 40 x 2cm (unframed)",
            dimensions_in="19.7 x 15.7 x 0.8in",
            is_available_for_sale=True,
            image_url="https://d2m7ibezl7l5lt.cloudfront.net/img/sunset.jpg",
            substrate="Canvas",
            materials="Oil paint",
            signature="Signed on the front",
            has_detail=True,
            source_file="page.html",
            crawled_at=CRAWLED_AT,
        )
    ]


def test_artfinder_artist_shop_cards_fall_back_to_usd():
    rows = map_page(
        "artfinder", builders.artfinder_artist([builders.artfinder_card(1), builders.artfinder_card(2)])
    )
    assert [(r.platform_artwork_id, r.price, r.currency) for r in rows] == [
        ("1", Decimal("300.00"), "USD"),
        ("2", Decimal("300.00"), "USD"),
    ]
    assert rows[0].styles == ["impressionism"]


def test_artmajeur_artwork_page():
    assert map_page("artmajeur", builders.artmajeur_artwork()) == [
        ArtworkRow(
            platform_artwork_id="901",
            title="Blue Sea",
            artist_name="Marie Curie",
            platform_artist_id="777",
            artwork_url="https://www.artmajeur.com/marie-curie/en/artworks/901/blue-sea",
            category="painting",
            width_cm=40.0,
            height_cm=50.0,
            depth_cm=2.0,
            price=Decimal("1200.00"),
            currency="USD",
            year=2020,
            subject="Seascape",
            description="Calm waves.",
            styles=["Abstract"],
            mediums=["Painting"],
            width_in=15.7,
            height_in=19.7,
            depth_in=0.8,
            is_available_for_sale=True,
            ships_from_country="France",
            dimensions="Height 50 cm, Width 40 cm, Depth 2 cm",
            is_sold=False,
            is_price_on_request=False,
            image_url="https://cdn.artmajeur.com/blue-sea.jpg",
            technique="Oil",
            support="Canvas",
            is_ai_generated=False,
            has_certificate=True,
            is_signed=True,
            has_detail=True,
            source_file="page.html",
            crawled_at=CRAWLED_AT,
        )
    ]


def test_artsper_artist_page_cards():
    rows = map_page(
        "artsper",
        builders.artsper_artist([builders.artsper_card(9), builders.artsper_card(10, price="Sold")]),
    )
    assert rows[0] == ArtworkRow(
        platform_artwork_id="9",
        title="Red",
        artist_name="Luca Rossi",
        platform_artist_id="555",
        artwork_url="https://www.artsper.com/us/contemporary-artworks/painting/9/red",
        category="painting",
        width_cm=85.0,
        height_cm=85.0,
        depth_cm=4.0,
        price=Decimal("2557.00"),
        currency="USD",
        mediums=["Painting"],
        dimensions_cm="85 x 85 x 4 cm",
        dimensions_in="33.5 x 33.5 x 1.6 in",
        width_in=33.5,
        height_in=33.5,
        depth_in=1.6,
        is_sold=False,
        is_price_on_request=False,
        image_url="https://media.artsper.com/9.jpg",
        has_detail=False,
        source_file="page.html",
        crawled_at=CRAWLED_AT,
    )
    sold = rows[1]
    assert (sold.platform_artwork_id, sold.is_sold, sold.price, sold.currency) == ("10", True, None, None)


def test_relevant_page_without_artworks_yields_no_rows():
    assert map_page("saatchi", builders.saatchi_artist()) == []


@pytest.mark.parametrize(
    ("platform", "html"),
    [("artsper", builders.artsper_listing()), ("saatchi", "<html><body></body></html>")],
)
def test_pages_without_artworks_are_skipped(platform, html):
    assert not ARTWORKS.mapper(platform).is_relevant(parse_html(platform, html))


def _sample_files() -> list[tuple[str, Path]]:
    return [(p, f) for p in ARTWORKS.platforms for f in sorted((SAMPLES_DIR / p).rglob("*.html"))]


@pytest.mark.skipif(not _sample_files(), reason="no real samples in etl/samples/<platform>/")
@pytest.mark.parametrize(("platform", "path"), _sample_files(), ids=lambda v: getattr(v, "name", v))
def test_real_samples_map_to_valid_artwork_rows(platform, path):
    parsed = parse_html(platform, path.read_text(encoding="utf-8", errors="replace"))
    mapper = ARTWORKS.mapper(platform)
    if not mapper.is_relevant(parsed):
        pytest.skip(f"{parsed.get('page_type')} page")
    rows = mapper.to_rows(parsed, path.name, CRAWLED_AT)
    to_table(ARTWORK_SCHEMA, rows)
    for row in rows:
        assert row.platform_artwork_id
        assert row.artwork_url is None or row.artwork_url.startswith("http")
        assert row.price is not None or row.currency is None

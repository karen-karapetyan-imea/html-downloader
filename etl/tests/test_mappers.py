from datetime import UTC, datetime
from pathlib import Path

import pytest

from artists.mappers import get_mapper
from artists.schema import ArtistRow
from artists.sources import PLATFORMS, parse_html
from tests import builders

CRAWLED_AT = datetime(2026, 9, 23, tzinfo=UTC)
SAMPLES_DIR = Path(__file__).resolve().parent.parent / "samples"


def map_page(platform: str, html: str, url: str | None = None) -> ArtistRow | None:
    parsed = parse_html(platform, html, url)
    mapper = get_mapper(platform)
    assert mapper.is_artist_page(parsed), f"{platform} page not detected as artist page"
    return mapper.to_artist(parsed, "page.html", CRAWLED_AT)


def test_saatchi():
    assert map_page("saatchi", builders.saatchi_artist()) == ArtistRow(
        platform_artist_id="123",
        full_name="Jane Doe",
        profile_url="https://www.saatchiart.com/jane-doe",
        avatar="https://images.saatchiart.com/avatar.jpg",
        biography="Painter.\nSecond & last.",
        website="https://janedoe.com",
        facebook="https://facebook.com/janedoe",
        instagram="https://www.instagram.com/janedoe",
        twitter="https://x.com/janedoe",
        city="Berlin",
        state="Berlin",
        zip_code="10115",
        country="Germany",
        source_file="page.html",
        crawled_at=CRAWLED_AT,
    )


def test_artsy():
    assert map_page("artsy", builders.artsy_artist()) == ArtistRow(
        platform_artist_id="4d8b92b34eb68a1b2c0003f4",
        full_name="Andy Warhol",
        profile_url="https://www.artsy.net/artist/andy-warhol",
        avatar="https://d32dm0rphc51dk.cloudfront.net/warhol.jpg",
        birth_year=1928,
        death_year=1987,
        gender="male",
        biography="Pop art pioneer.",
        nationality="American",
        source_file="page.html",
        crawled_at=CRAWLED_AT,
    )


def test_artfinder():
    assert map_page("artfinder", builders.artfinder_artist()) == ArtistRow(
        platform_artist_id="42",
        full_name="John Smith",
        profile_url="https://www.artfinder.com/artist/john-smith/",
        avatar="https://d2m7ibezl7l5lt.cloudfront.net/img/avatar.jpg",
        biography="Landscape painter.",
        website="https://www.johnsmithart.co.uk",
        facebook="https://www.facebook.com/johnsmithart",
        instagram="https://www.instagram.com/johnsmith.art",
        tiktok="https://www.tiktok.com/@johnsmith",
        twitter="https://twitter.com/jsmith",
        country="United Kingdom",
        source_file="page.html",
        crawled_at=CRAWLED_AT,
    )


def test_artmajeur():
    assert map_page("artmajeur", builders.artmajeur_artist()) == ArtistRow(
        platform_artist_id="777",
        full_name="Marie Curie",
        profile_url="https://www.artmajeur.com/marie-curie",
        avatar="https://cdn.artmajeur.com/portrait.jpg",
        birth_year=1980,
        gender="female",
        biography="First paragraph.\n\nSecond paragraph.",
        website="https://www.mariecurie-art.fr",
        facebook="https://www.facebook.com/mariepaints",
        instagram="https://www.instagram.com/marie.paints",
        nationality="French",
        city="Lyon",
        state="Auvergne-Rhone-Alpes",
        zip_code="69001",
        country="France",
        source_file="page.html",
        crawled_at=CRAWLED_AT,
    )


def test_artsper():
    assert map_page("artsper", builders.artsper_artist()) == ArtistRow(
        platform_artist_id="555",
        full_name="Luca Rossi",
        profile_url="https://www.artsper.com/us/contemporary-artists/italy/555/luca-rossi",
        avatar="https://media.artsper.com/luca.jpg",
        birth_year=1975,
        biography="Long bio.\nMore.",
        website="https://lucarossi.it",
        instagram="https://instagram.com/lucarossi",
        nationality="Italian",
        source_file="page.html",
        crawled_at=CRAWLED_AT,
    )


@pytest.mark.parametrize(
    ("platform", "html"),
    [("saatchi", builders.saatchi_artwork()), ("artsper", builders.artsper_listing())],
)
def test_non_artist_pages_are_skipped(platform, html):
    assert not get_mapper(platform).is_artist_page(parse_html(platform, html))


def _sample_files() -> list[tuple[str, Path]]:
    return [(p, f) for p in PLATFORMS for f in sorted((SAMPLES_DIR / p).rglob("*.html"))]


@pytest.mark.skipif(not _sample_files(), reason="no real samples in etl/samples/<platform>/")
@pytest.mark.parametrize(("platform", "path"), _sample_files(), ids=lambda v: getattr(v, "name", v))
def test_real_samples_have_core_fields(platform, path):
    parsed = parse_html(platform, path.read_text(encoding="utf-8", errors="replace"))
    mapper = get_mapper(platform)
    if not mapper.is_artist_page(parsed):
        pytest.skip(f"{parsed.get('page_type')} page")
    row = mapper.to_artist(parsed, path.name, CRAWLED_AT)
    assert row is not None
    assert row.platform_artist_id
    assert row.full_name
    assert row.profile_url and row.profile_url.startswith("http")

from __future__ import annotations

from pathlib import Path

from sitemap_crawler.crawler import CrawlConfig, SitemapCrawler
from sitemap_crawler.entities import classify_url, is_english_url
from sitemap_crawler.normalizer import normalize_sitemap_page_url, normalize_url
from sitemap_crawler.parser import (
    ART_SITEMAP_SEEDS,
    discover_sitemap_root,
    is_art_sitemap_page,
    is_sitemap_page,
    parse_page_links,
)
from sitemap_crawler.storage import CrawlerState, write_art_outputs, write_output

HOMEPAGE_HTML = """
<html>
<body>
<footer>
  <a href="/sitemap/">Sitemap</a>
  <a href="/sitemap/art/">Art Sitemap</a>
  <a href="https://example.com/sitemap/">External</a>
</footer>
</body>
</html>
"""

SITEMAP_PAGE_HTML = """
<html><body>
<a href="/sitemap/art/items/">Items</a>
<a href="/sitemap/furniture/items/">Furniture</a>
<a href="/art/paintings/foo/id-a_12345/">Item</a>
<a href="/dealers/gallery-one/">Dealer</a>
<a href="/dealers/gallery-one/shop/">Dealer shop</a>
<a href="/creators/pablo-picasso/art/paintings/">Creator</a>
<a href="/creators/jewelry/">Jewelry hub</a>
<a href="mailto:info@1stdibs.com">Email</a>
<a href="#section">Fragment</a>
<a href="/art/paintings/foo/id-a_12345/#tab">Item fragment</a>
<a href="https://www.1stdibs.com/foo?utm_source=x&page=2">Tracked</a>
</body></html>
"""

ART_ITEMS_SITEMAP_HTML = """
<html><body>
<a href="/sitemap/art/items/3/1/">Child</a>
</body></html>
"""


def test_normalize_url_strips_fragment_and_utm() -> None:
    assert (
        normalize_url("https://www.1stdibs.com/foo/#abc")
        == "https://www.1stdibs.com/foo/"
    )
    assert (
        normalize_url("https://www.1stdibs.com/foo?utm_source=x&page=2")
        == "https://www.1stdibs.com/foo?page=2"
    )
    assert normalize_url("https://example.com/foo") is None


def test_normalize_url_preserves_trailing_slash_difference() -> None:
    with_slash = normalize_url("https://www.1stdibs.com/foo/")
    without_slash = normalize_url("https://www.1stdibs.com/foo")
    assert with_slash == "https://www.1stdibs.com/foo/"
    assert without_slash == "https://www.1stdibs.com/foo"


def test_is_sitemap_page() -> None:
    assert is_sitemap_page("https://www.1stdibs.com/sitemap/")
    assert is_sitemap_page("https://www.1stdibs.com/sitemap/art/items/")
    assert not is_sitemap_page("https://www.1stdibs.com/art/paintings/")
    assert not is_sitemap_page("https://example.com/sitemap/")


def test_is_art_sitemap_page() -> None:
    assert is_art_sitemap_page("https://www.1stdibs.com/sitemap/art/items/")
    assert is_art_sitemap_page("https://www.1stdibs.com/sitemap/art/items/3/1/")
    assert is_art_sitemap_page("https://www.1stdibs.com/sitemap/art/dealers/")
    assert is_art_sitemap_page("https://www.1stdibs.com/sitemap/art/creators/")
    assert not is_art_sitemap_page("https://www.1stdibs.com/sitemap/")
    assert not is_art_sitemap_page("https://www.1stdibs.com/sitemap/furniture/items/")


def test_discover_sitemap_root_from_homepage() -> None:
    root = discover_sitemap_root(HOMEPAGE_HTML, "https://www.1stdibs.com/")
    assert root == "https://www.1stdibs.com/sitemap/"


def test_parse_page_links_filters_non_art_sitemap_children() -> None:
    internal, sitemap_children = parse_page_links(
        SITEMAP_PAGE_HTML,
        "https://www.1stdibs.com/sitemap/art/items/",
    )
    assert "https://www.1stdibs.com/sitemap/art/items/" in sitemap_children
    assert all("/sitemap/furniture/" not in url for url in sitemap_children)
    assert "https://www.1stdibs.com/art/paintings/foo/id-a_12345/" in internal
    assert "https://www.1stdibs.com/dealers/gallery-one/" in internal


def test_normalize_sitemap_page_url() -> None:
    assert (
        normalize_sitemap_page_url("https://www.1stdibs.com/sitemap/art/items")
        == "https://www.1stdibs.com/sitemap/art/items/"
    )


def test_classify_artwork_url() -> None:
    url = "https://www.1stdibs.com/art/paintings/foo/id-a_12345/"
    assert classify_url(url) == ("item", "12345", url)


def test_classify_dealer_url() -> None:
    url = "https://www.1stdibs.com/dealers/gallery-one/"
    assert classify_url(url) == ("dealer", "gallery-one", url)


def test_reject_dealer_shop_url() -> None:
    url = "https://www.1stdibs.com/dealers/gallery-one/shop/"
    assert classify_url(url) is None


def test_classify_creator_url_prefers_art_path() -> None:
    url = "https://www.1stdibs.com/creators/pablo-picasso/art/paintings/"
    assert classify_url(url) == (
        "creator",
        "pablo-picasso",
        "https://www.1stdibs.com/creators/pablo-picasso/art/",
    )


def test_reject_creator_hubs_and_locales() -> None:
    assert classify_url("https://www.1stdibs.com/creators/jewelry/") is None
    assert classify_url("https://www.1stdibs.com/creators/cartier/jewelry/") is None
    assert classify_url("https://www.1stdibs.com/fr/art/foo/id-a_1/") is None
    assert not is_english_url("https://www.1stdibs.com/de/art/foo/")


def test_crawler_state_resume_round_trip(tmp_path: Path) -> None:
    state = CrawlerState(tmp_path / "state")
    state.visited_sitemap_pages = {"https://www.1stdibs.com/sitemap/art/items/"}
    state.pending_sitemap_pages = ["https://www.1stdibs.com/sitemap/art/items/3/1/"]
    state.artwork_urls = {"999": "https://www.1stdibs.com/art/foo/id-a_999/"}
    state.dealer_urls = {"gallery": "https://www.1stdibs.com/dealers/gallery/"}
    state.save()

    reloaded = CrawlerState(tmp_path / "state")
    reloaded.load()
    assert reloaded.visited_sitemap_pages == state.visited_sitemap_pages
    assert reloaded.pending_sitemap_pages == state.pending_sitemap_pages
    assert reloaded.artwork_urls == state.artwork_urls
    assert reloaded.dealer_urls == state.dealer_urls


def test_pending_filtered_to_art_on_load(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "pending_sitemaps.txt").write_text(
        "\n".join(
            [
                "https://www.1stdibs.com/sitemap/art/items/3/1/",
                "https://www.1stdibs.com/sitemap/furniture/items/3/1/",
            ]
        ),
        encoding="utf-8",
    )
    state = CrawlerState(state_dir)
    state.load()
    assert state.pending_sitemap_pages == [
        "https://www.1stdibs.com/sitemap/art/items/3/1/",
    ]


def test_migrate_legacy_discovered_urls(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "discovered_urls.txt").write_text(
        "\n".join(
            [
                "https://www.1stdibs.com/dealers/gallery-one/",
                "https://www.1stdibs.com/dealers/gallery-one/shop/",
                "https://www.1stdibs.com/sitemap/art/items/3/1/",
            ]
        ),
        encoding="utf-8",
    )
    state = CrawlerState(state_dir)
    state.load()
    assert state.dealer_urls == {
        "gallery-one": "https://www.1stdibs.com/dealers/gallery-one/",
    }
    assert state.artwork_urls == {}


def test_write_art_outputs(tmp_path: Path) -> None:
    paths = write_art_outputs(
        tmp_path / "output",
        artwork_urls={"1": "https://www.1stdibs.com/art/foo/id-a_1/"},
        dealer_urls={"gallery": "https://www.1stdibs.com/dealers/gallery/"},
        creator_urls={
            "picasso": "https://www.1stdibs.com/creators/picasso/art/",
        },
    )
    assert paths["artwork"].read_text(encoding="utf-8").strip().endswith("id-a_1/")
    assert paths["all"].read_text(encoding="utf-8").count("\n") == 3


def test_write_output_legacy(tmp_path: Path) -> None:
    urls = {
        "https://www.1stdibs.com/art/foo/id-a_1",
        "https://www.1stdibs.com/dealers/gallery/",
    }
    txt_path, json_path = write_output(
        tmp_path / "output",
        source="https://www.1stdibs.com/",
        sitemap_url="https://www.1stdibs.com/sitemap/",
        sitemap_pages_crawled=3,
        urls=urls,
    )
    assert txt_path.is_file()
    assert json_path.is_file()
    assert txt_path.read_text(encoding="utf-8").count("\n") == 2


def test_crawler_art_scope_and_dedup(tmp_path: Path) -> None:
    pages = {
        "https://www.1stdibs.com/sitemap/art/items/": ART_ITEMS_SITEMAP_HTML,
        "https://www.1stdibs.com/sitemap/art/items/3/1/": """
        <html><body>
        <a href="/art/paintings/foo/id-a_999/">Item</a>
        <a href="/art/paintings/foo/id-a_999/">Duplicate</a>
        <a href="/sitemap/furniture/items/3/1/">Furniture</a>
        </body></html>
        """,
    }

    def fake_fetch(url: str) -> str:
        return pages[url]

    state = CrawlerState(tmp_path / "state")
    state.pending_sitemap_pages = [ART_SITEMAP_SEEDS[0]]
    crawler = SitemapCrawler(
        state=state,
        config=CrawlConfig(delay=0, concurrency=2, checkpoint_every=1),
        fetcher=fake_fetch,
    )
    result = crawler.run()

    assert result.artwork_urls == 1
    assert result.dealer_urls == 0
    assert "https://www.1stdibs.com/sitemap/furniture/items/3/1/" not in state.pending_sitemap_pages
    assert state.artwork_urls["999"] == "https://www.1stdibs.com/art/paintings/foo/id-a_999/"

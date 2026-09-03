from __future__ import annotations

from pathlib import Path

from html_downloader.discover.firstdibs import (
    DEFAULT_1STDIBS_ART_CATEGORIES,
    DEFAULT_1STDIBS_DEALER_SEED,
    _looks_like_bot_challenge,
    browse_page_url,
    extract_browse_item_urls,
    extract_entity_urls,
    extract_sitemap_links,
    fetch_firstdibs_browse_item_entries,
    fetch_firstdibs_dealer_sitemap_entries,
    fetch_firstdibs_sitemap_entries,
    parse_browse_total_results,
)
from html_downloader.discover.sitemap import known_keys_from_sources

ITEMS_INDEX_HTML = """
<html><body>
<a href="/sitemap/art/items/3/6001/">Leaf</a>
<a href="/sitemap/art/style/contemporary/">Skip style</a>
</body></html>
"""

ITEMS_LEAF_HTML = """
<html><body>
<a href="/art/paintings/abstract/kathleen-rhee-summer/id-a_12383202/">Item</a>
</body></html>
"""

DEALER_LEAF_HTML = """
<html><body>
<a href="/dealers/1-drop-gallery/">Dealer</a>
<a href="/dealers/1-drop-gallery/shop/art/paintings/">Dealer shop</a>
</body></html>
"""

BROWSE_PAGE_HTML = """
<html><body>
<script>{"totalResults":45,"pageType":"browse"}</script>
<a href="/art/paintings/abstract/foo/id-a_111/">Item 1</a>
<a href="/art/paintings/landscape/bar/id-a_222/">Item 2</a>
</body></html>
"""

BROWSE_PAGE_2_HTML = """
<html><body>
<a href="/art/paintings/figurative/baz/id-a_333/">Item 3</a>
</body></html>
"""

BROWSE_PAGE_3_HTML = """
<html><body>
</body></html>
"""


def test_parse_browse_total_results() -> None:
    assert parse_browse_total_results(BROWSE_PAGE_HTML) == 45
    assert parse_browse_total_results("<html></html>") is None


def test_browse_page_url() -> None:
    category = "https://www.1stdibs.com/art/paintings/"
    assert browse_page_url(category, 1) == "https://www.1stdibs.com/art/paintings/"
    assert browse_page_url(category, 2) == "https://www.1stdibs.com/art/paintings/?page=2"


def test_extract_browse_item_urls() -> None:
    urls = extract_browse_item_urls(
        BROWSE_PAGE_HTML,
        "https://www.1stdibs.com/art/paintings/?page=1",
    )
    assert urls == [
        "https://www.1stdibs.com/art/paintings/abstract/foo/id-a_111",
        "https://www.1stdibs.com/art/paintings/landscape/bar/id-a_222",
    ]


def test_fetch_firstdibs_browse_item_entries_pagination() -> None:
    category = DEFAULT_1STDIBS_ART_CATEGORIES[0]
    fixtures = {
        browse_page_url(category, 1): BROWSE_PAGE_HTML,
        browse_page_url(category, 2): BROWSE_PAGE_2_HTML,
        browse_page_url(category, 3): BROWSE_PAGE_3_HTML,
    }
    requested: list[str] = []

    def fake_fetch(url: str) -> str:
        requested.append(url)
        return fixtures[url]

    entries = fetch_firstdibs_browse_item_entries(
        categories=[category],
        fetch_html=fake_fetch,
        concurrency=2,
    )
    by_key = {(e.entity_type, e.entity_id): e for e in entries}
    assert ("item", "111") in by_key
    assert ("item", "222") in by_key
    assert ("item", "333") in by_key
    assert len(by_key) == 3
    assert browse_page_url(category, 1) in requested
    assert browse_page_url(category, 2) in requested
    assert browse_page_url(category, 3) in requested


def test_extract_sitemap_links_filters_art_branches() -> None:
    base = "https://www.1stdibs.com/sitemap/art/dealers/"
    links = extract_sitemap_links(
        '<html><body><a href="/sitemap/art/dealers/1-drop-gallery/">Dealer</a>'
        '<a href="/sitemap/art/items/3/6001/">Skip items</a></body></html>',
        base,
    )
    assert links == [
        "https://www.1stdibs.com/sitemap/art/dealers/1-drop-gallery/",
    ]


def test_extract_entity_urls_from_leaf_pages() -> None:
    item_urls = extract_entity_urls(
        ITEMS_LEAF_HTML,
        "https://www.1stdibs.com/sitemap/art/items/3/6001/",
    )
    assert item_urls == [
        "https://www.1stdibs.com/art/paintings/abstract/kathleen-rhee-summer/id-a_12383202",
    ]

    dealer_urls = extract_entity_urls(
        DEALER_LEAF_HTML,
        "https://www.1stdibs.com/sitemap/art/dealers/1-drop-gallery/",
    )
    assert dealer_urls == [
        "https://www.1stdibs.com/dealers/1-drop-gallery",
        "https://www.1stdibs.com/dealers/1-drop-gallery/shop/art/paintings",
    ]


def test_fetch_firstdibs_dealer_sitemap_entries_bfs() -> None:
    fixtures = {
        DEFAULT_1STDIBS_DEALER_SEED: (
            '<html><body><a href="/sitemap/art/dealers/1-drop-gallery/">Dealer</a></body></html>'
        ),
        "https://www.1stdibs.com/sitemap/art/dealers/1-drop-gallery/": DEALER_LEAF_HTML,
    }

    def fake_fetch(url: str) -> str:
        normalized = url if url.endswith("/") else url + "/"
        return fixtures[normalized]

    entries = fetch_firstdibs_dealer_sitemap_entries(fetch_html=fake_fetch, concurrency=2)
    by_key = {(e.entity_type, e.entity_id): e for e in entries}
    assert ("dealer", "1-drop-gallery") in by_key
    assert by_key[("dealer", "1-drop-gallery")].url == "https://www.1stdibs.com/dealers/1-drop-gallery/"
    assert len(by_key) == 1


def test_fetch_firstdibs_sitemap_entries_combined(tmp_path: Path) -> None:
    fixtures = {
        "https://www.1stdibs.com/sitemap/art/items/": ITEMS_INDEX_HTML,
        "https://www.1stdibs.com/sitemap/art/items/3/6001/": ITEMS_LEAF_HTML,
        "https://www.1stdibs.com/sitemap/art/dealers/": (
            '<html><body><a href="/sitemap/art/dealers/1-drop-gallery/">Dealer</a></body></html>'
        ),
        "https://www.1stdibs.com/sitemap/art/dealers/1-drop-gallery/": DEALER_LEAF_HTML,
        "https://www.1stdibs.com/sitemap/art/creators/": (
            '<html><body><a href="/creators/pablo-picasso/art/paintings/">Creator</a></body></html>'
        ),
    }

    def fake_fetch(url: str) -> str:
        normalized = url if url.endswith("/") else url + "/"
        return fixtures[normalized]

    entries = fetch_firstdibs_sitemap_entries(
        fetch_html=fake_fetch,
        concurrency=2,
        state_dir=tmp_path / "state",
        delay=0,
    )
    by_key = {(e.entity_type, e.entity_id): e for e in entries}
    assert ("item", "12383202") in by_key
    assert ("dealer", "1-drop-gallery") in by_key
    assert ("creator", "pablo-picasso") in by_key
    assert by_key[("creator", "pablo-picasso")].url.endswith("/creators/pablo-picasso/art/")
    assert len(by_key) == 3


def test_known_keys_from_sources_firstdibs(tmp_path: Path) -> None:
    path = tmp_path / "urls.txt"
    path.write_text(
        "https://www.1stdibs.com/art/paintings/foo/id-a_99999/\n",
        encoding="utf-8",
    )
    keys = known_keys_from_sources(known_paths=[path], source="firstdibs")
    assert keys == {("item", "99999")}


def test_bot_challenge_allows_1stdibs_sitemap_with_fs_ch_script() -> None:
    html = b"""<!doctype html><html><head>
    <script data-client-detection src="/_fs-ch-abc/assets/script.js"></script>
    <title>Items Art Sitemap - 1stDibs</title></head>
    <body><a href="/sitemap/art/items/3/1/">leaf</a></body></html>"""
    assert _looks_like_bot_challenge(html) is False


def test_bot_challenge_allows_browse_page() -> None:
    html = b"""<html><body>
    ItemSearchQuery
    <a href="/art/paintings/foo/id-a_123/">item</a>
    </body></html>"""
    assert _looks_like_bot_challenge(html) is False


def test_bot_challenge_rejects_captcha_page() -> None:
    html = b"<html><body><div id=\"captcha-container\"></div></body></html>"
    assert _looks_like_bot_challenge(html) is True

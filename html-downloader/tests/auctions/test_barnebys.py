from __future__ import annotations

import json
from pathlib import Path

from html_downloader.auctions.barnebys import (
    fetch_barnebys_sitemap_entries,
    is_barnebys_lot_sitemap,
    load_sitemap_progress,
    parse_barnebys_entries,
    save_sitemap_progress,
    select_child_sitemaps,
)
from html_downloader.auctions.barnebys_algolia import (
    append_lot_cache,
    build_live_lot_url,
    build_result_lot_url,
    hit_to_sitemap_entries,
    iter_lot_cache_rows,
    load_lot_cache_keys,
    lot_cache_path,
)
from html_downloader.auctions.paths import auction_sitemap_progress_file
from html_downloader.discover.sitemap import SitemapEntry

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def test_parse_barnebys_entries_dedupes_and_emits_realized() -> None:
    xml = (FIXTURES / "barnebys_lots_child.xml").read_bytes()
    entries = parse_barnebys_entries(xml, include_realized=True)
    by_key = {e.entity_key: e for e in entries}
    assert ("lot", "629739503") in by_key
    assert ("result_lot", "629739503") in by_key
    assert ("lot", "629739658") in by_key
    assert ("result_lot", "629739658") in by_key
    assert by_key[("lot", "629739503")].url.endswith(
        "/auctions/lot/f-clark-oil-painting-gGZS28C-629739503"
    )
    assert by_key[("result_lot", "629739503")].url.endswith(
        "/realized-prices/lot/f-clark-oil-painting-gGZS28C-629739503"
    )
    assert all("/auctions/all" not in e.url for e in entries)
    assert all("/blog/" not in e.url for e in entries)


def test_parse_barnebys_entries_live_only() -> None:
    xml = (FIXTURES / "barnebys_lots_child.xml").read_bytes()
    entries = parse_barnebys_entries(xml, include_realized=False)
    assert all(e.entity_type == "lot" for e in entries)
    assert {e.entity_id for e in entries} == {"629739503", "629739658"}


def test_lot_sitemap_filter() -> None:
    assert is_barnebys_lot_sitemap(
        "https://www.barnebys.com/sitemap/lots-0.xml.gz"
    )
    assert is_barnebys_lot_sitemap(
        "https://www.barnebys.com/sitemap/lots-7.xml.gz"
    )
    assert not is_barnebys_lot_sitemap(
        "https://www.barnebys.com/sitemap/keywords.xml.gz"
    )
    assert not is_barnebys_lot_sitemap(
        "https://www.barnebys.com/sitemap/pages.xml.gz"
    )


def test_select_child_sitemaps_skips_done_and_non_lots() -> None:
    children = [
        "https://www.barnebys.com/sitemap/lots-0.xml.gz",
        "https://www.barnebys.com/sitemap/lots-1.xml.gz",
        "https://www.barnebys.com/sitemap/keywords.xml.gz",
    ]
    progress = {
        children[0]: {"status": "done", "lots_found": 10},
        children[1]: {"status": "pending"},
    }
    selected = select_child_sitemaps(children, progress, max_sitemaps=5)
    assert selected == [children[1]]


def test_sitemap_progress_roundtrip(tmp_path: Path) -> None:
    path = auction_sitemap_progress_file(tmp_path, "barnebys")
    save_sitemap_progress(
        path,
        {
            "https://www.barnebys.com/sitemap/lots-0.xml.gz": {
                "status": "done",
                "lots_found": 3,
            }
        },
    )
    loaded = load_sitemap_progress(path)
    assert (
        loaded["https://www.barnebys.com/sitemap/lots-0.xml.gz"]["status"] == "done"
    )


def test_lot_cache_append_and_load(tmp_path: Path) -> None:
    progress_path = auction_sitemap_progress_file(tmp_path, "barnebys")
    cache = lot_cache_path(progress_path)
    append_lot_cache(
        cache,
        [
            SitemapEntry(
                url="https://www.barnebys.com/auctions/lot/foo-AbC12De-1",
                lastmod=None,
                entity_type="lot",
                entity_id="1",
            ),
            SitemapEntry(
                url="https://www.barnebys.com/realized-prices/lot/foo-AbC12De-1",
                lastmod=None,
                entity_type="result_lot",
                entity_id="1",
            ),
        ],
    )
    keys = load_lot_cache_keys(cache)
    assert keys == {("lot", "1"), ("result_lot", "1")}
    rows = list(iter_lot_cache_rows(cache))
    assert len(rows) == 2
    assert rows[0][0] == "lot"


def test_url_builders() -> None:
    assert (
        build_live_lot_url(lot_id="123", slug="Hello World", token="AbC12De")
        == "https://www.barnebys.com/auctions/lot/hello-world-AbC12De-123"
    )
    assert (
        build_result_lot_url(lot_id="123", slug="Hello World")
        == "https://www.barnebys.com/realized-prices/lot/123/hello-world"
    )


def test_hit_to_sitemap_entries() -> None:
    entries = hit_to_sitemap_entries(
        {"id": 999001, "slug": "Oil Painting", "token": "XxYyZz1"},
        include_realized=True,
    )
    assert len(entries) == 2
    assert entries[0].entity_type == "lot"
    assert entries[0].entity_id == "999001"
    assert entries[1].entity_type == "result_lot"


def test_hit_to_sitemap_entries_prefers_uid() -> None:
    entries = hit_to_sitemap_entries(
        {
            "id": "632129420",
            "uid": "signed-landscape-oil-painting-AjwyNnv-632129420",
            "category": "1",
            "title": "Signed Landscape Oil Painting",
        },
        include_realized=True,
        art_only=True,
    )
    assert len(entries) == 2
    assert entries[0].url.endswith(
        "/auctions/lot/signed-landscape-oil-painting-AjwyNnv-632129420"
    )
    assert entries[1].url.endswith(
        "/realized-prices/lot/signed-landscape-oil-painting-AjwyNnv-632129420"
    )


def test_hit_art_only_filters_non_art() -> None:
    entries = hit_to_sitemap_entries(
        {
            "id": "1",
            "uid": "car-part-AbC12De-1",
            "category": "25",
            "categoryName": "Vehicles",
        },
        art_only=True,
    )
    assert entries == []


def test_lot_urls_from_html_and_totals() -> None:
    from html_downloader.auctions.barnebys_algolia import (
        lot_urls_from_html,
        parse_realized_totals,
    )

    html = (
        r'totalResults\":123,\"totalPages\":4,'
        r'href="/realized-prices/lot/oil-painting-AbC12De-999001" '
        r'href="/realized-prices/lot/oil-painting-AbC12De-999001"'
    )
    total, pages = parse_realized_totals(html)
    assert total == 123
    assert pages == 4
    entries = lot_urls_from_html(html)
    keys = {e.entity_key for e in entries}
    assert ("result_lot", "999001") in keys
    assert ("lot", "999001") in keys


def test_build_art_partitions_seed() -> None:
    from html_downloader.auctions.barnebys_algolia import build_art_partitions

    parts = build_art_partitions(
        queries=("painting",),
        category_slugs=("contemporary-art",),
        include_live=True,
        include_realized=True,
        deep_realized=False,
    )
    keys = {p.key for p in parts}
    assert keys == {"live:painting", "realized:contemporary-art"}


def test_expand_lots_from_search_mocked(tmp_path: Path) -> None:
    from html_downloader.auctions.barnebys_algolia import expand_lots_from_algolia

    state = tmp_path / "barnebys_search.json"
    live_payload = json.dumps(
        {
            "hits": [
                {
                    "id": "111",
                    "uid": "oil-painting-AaAaAa1-111",
                    "category": "1",
                    "categoryName": "Arts & Graphics",
                    "title": "Oil",
                }
            ],
            "total": 1,
            "pages": 1,
            "page": 1,
            "perPage": 30,
        }
    )
    realized_html = (
        r'totalResults\":1,\"totalPages\":1,'
        r'"/realized-prices/lot/bronze-sculpture-BbBbBb2-222"'
    )

    def fetch_text(url: str) -> str:
        if "/api/search" in url:
            if "page=2" in url or "page=3" in url:
                return json.dumps(
                    {"hits": [], "total": 1, "pages": 1, "page": 2, "perPage": 30}
                )
            return live_payload
        return realized_html

    entries = expand_lots_from_algolia(
        state_path=state,
        fetch_text=fetch_text,
        delay=0,
        queries=("painting",),
        category_slugs=("sculptures",),
        art_only=True,
        live_max_pages=3,
        realized_max_pages=3,
    )
    keys = {(e.entity_type, e.entity_id) for e in entries}
    assert ("lot", "111") in keys
    assert ("result_lot", "111") in keys
    assert ("lot", "222") in keys
    assert ("result_lot", "222") in keys
    # Resume: second run adds nothing
    again = expand_lots_from_algolia(
        state_path=state,
        fetch_text=fetch_text,
        delay=0,
        queries=("painting",),
        category_slugs=("sculptures",),
        art_only=True,
    )
    assert again == []


def test_fetch_barnebys_from_index_fixture(tmp_path: Path) -> None:
    index_xml = (FIXTURES / "barnebys_sitemap_index.xml").read_bytes()
    child_xml = (FIXTURES / "barnebys_lots_child.xml").read_bytes()
    progress = auction_sitemap_progress_file(tmp_path, "barnebys")

    def fetch_bytes(url: str) -> bytes:
        if url.endswith("sitemap.xml") or "index" in url:
            return index_xml
        if "lots-0" in url or "lots-1" in url:
            return child_xml
        raise RuntimeError(f"unexpected url {url}")

    # Point seed at a fake index URL that our fetcher recognizes
    entries = fetch_barnebys_sitemap_entries(
        "https://www.barnebys.com/sitemap.xml",
        fetch_bytes=fetch_bytes,
        max_sitemaps=2,
        min_urls=10_000,
        sitemap_progress_path=progress,
        include_realized=True,
    )
    assert len(entries) >= 4
    cache = lot_cache_path(progress)
    assert cache.is_file()
    assert len(load_lot_cache_keys(cache)) >= 4
    # Second run should yield no new rows (cache hit)
    again = fetch_barnebys_sitemap_entries(
        "https://www.barnebys.com/sitemap.xml",
        fetch_bytes=fetch_bytes,
        max_sitemaps=2,
        min_urls=10_000,
        sitemap_progress_path=progress,
        include_realized=True,
    )
    assert again == []

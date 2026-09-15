from __future__ import annotations

import json
from pathlib import Path

from html_downloader.auctions.liveauctioneers import (
    append_lot_cache,
    clear_sitemap_cache,
    fetch_liveauctioneers_sitemap_entries,
    is_liveauctioneers_price_result_sitemap,
    iter_lot_cache_rows,
    load_lot_cache_ids,
    load_sitemap_progress,
    lot_cache_path,
    parse_liveauctioneers_entries,
    save_sitemap_progress,
    select_child_sitemaps,
)
from html_downloader.auctions.paths import auction_sitemap_progress_file
from html_downloader.discover.sitemap import SitemapEntry

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def test_parse_price_result_entries_dedupes_and_filters() -> None:
    xml = (FIXTURES / "la_price_result_child.xml").read_bytes()
    entries = parse_liveauctioneers_entries(xml)
    by_key = {e.entity_key: e for e in entries}
    assert ("price_result", "oil-painting-123") in by_key
    assert ("price_result", "bronze-sculpture-abc") in by_key
    assert by_key[("price_result", "oil-painting-123")].lastmod == "2026-09-03"
    assert by_key[("price_result", "oil-painting-123")].url == (
        "https://www.liveauctioneers.com/price-result/oil-painting-123"
    )
    assert by_key[("price_result", "bronze-sculpture-abc")].url == (
        "https://www.liveauctioneers.com/price-result/bronze-sculpture-abc"
    )
    assert all("/item/" not in e.url for e in entries)


def test_price_result_sitemap_filter() -> None:
    assert is_liveauctioneers_price_result_sitemap(
        "https://www.liveauctioneers.com/price-result-sitemap-1.xml.gz"
    )
    assert not is_liveauctioneers_price_result_sitemap(
        "https://www.liveauctioneers.com/other-sitemap.xml.gz"
    )


def test_select_child_sitemaps_skips_done_and_limits() -> None:
    children = [
        "https://www.liveauctioneers.com/price-result-sitemap-1.xml.gz",
        "https://www.liveauctioneers.com/price-result-sitemap-2.xml.gz",
        "https://www.liveauctioneers.com/price-result-sitemap-3.xml.gz",
        "https://www.liveauctioneers.com/other-sitemap.xml.gz",
    ]
    progress = {
        children[0]: {"status": "done", "lots_found": 10},
        children[1]: {"status": "pending"},
    }
    selected = select_child_sitemaps(children, progress, max_sitemaps=1)
    assert selected == [children[1]]
    selected2 = select_child_sitemaps(children, progress, max_sitemaps=5)
    assert selected2 == [children[1], children[2]]


def test_select_child_sitemaps_prefers_dense_shards() -> None:
    sparse = "https://www.liveauctioneers.com/sitemap-price-result-a.xml.gz"
    dense = "https://www.liveauctioneers.com/sitemap-price-result--a-0.xml.gz"
    other = "https://www.liveauctioneers.com/sitemap-price-result-b.xml.gz"
    selected = select_child_sitemaps(
        [sparse, other, dense],
        {},
        max_sitemaps=3,
    )
    assert selected[0] == dense
    assert set(selected[1:]) == {sparse, other}


def test_sitemap_progress_roundtrip(tmp_path: Path) -> None:
    path = auction_sitemap_progress_file(tmp_path, "liveauctioneers")
    save_sitemap_progress(
        path,
        {
            "https://www.liveauctioneers.com/price-result-sitemap-1.xml.gz": {
                "status": "done",
                "lots_found": 3,
            }
        },
    )
    loaded = load_sitemap_progress(path)
    assert (
        loaded["https://www.liveauctioneers.com/price-result-sitemap-1.xml.gz"]["status"]
        == "done"
    )


def test_lot_cache_append_and_load(tmp_path: Path) -> None:
    progress_path = auction_sitemap_progress_file(tmp_path, "liveauctioneers")
    cache = lot_cache_path(progress_path)
    entries = [
        SitemapEntry(
            url="https://www.liveauctioneers.com/price-result/oil-painting-123",
            lastmod="2026-09-01",
            entity_type="price_result",
            entity_id="oil-painting-123",
        ),
        SitemapEntry(
            url="https://www.liveauctioneers.com/price-result/bronze-sculpture-abc",
            lastmod=None,
            entity_type="price_result",
            entity_id="bronze-sculpture-abc",
        ),
    ]
    append_lot_cache(cache, entries)
    assert load_lot_cache_ids(cache) == {
        "oil-painting-123",
        "bronze-sculpture-abc",
    }
    rows = list(iter_lot_cache_rows(cache))
    assert rows[0] == (
        "oil-painting-123",
        "https://www.liveauctioneers.com/price-result/oil-painting-123",
        "2026-09-01",
    )


def test_fetch_expands_index_with_max_sitemaps(tmp_path: Path) -> None:
    index_xml = (FIXTURES / "la_price_result_index.xml").read_bytes()
    child_xml = (FIXTURES / "la_price_result_child.xml").read_bytes()
    progress_path = auction_sitemap_progress_file(tmp_path, "liveauctioneers")
    cache = lot_cache_path(progress_path)

    def fetch_bytes(url: str) -> bytes:
        if "index" in url or url.endswith("index.xml.gz"):
            return index_xml
        if "price-result-sitemap-1" in url:
            return child_xml
        if "price-result-sitemap-2" in url:
            # Second child: one extra lot
            return b"""<?xml version="1.0"?>
            <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
              <url><loc>https://www.liveauctioneers.com/price-result/second-lot/</loc></url>
            </urlset>
            """
        raise AssertionError(f"unexpected url {url}")

    entries = fetch_liveauctioneers_sitemap_entries(
        "https://www.liveauctioneers.com/price-result-sitemap-index.xml.gz",
        fetch_bytes=fetch_bytes,
        max_sitemaps=1,
        min_urls=1,
        sitemap_progress_path=progress_path,
        max_retries=1,
    )
    assert len(entries) == 2
    progress = load_sitemap_progress(progress_path)
    assert (
        progress["https://www.liveauctioneers.com/price-result-sitemap-1.xml.gz"]["status"]
        == "done"
    )
    assert load_lot_cache_ids(cache) == {
        "oil-painting-123",
        "bronze-sculpture-abc",
    }

    # Second run expands the next pending child; returns only new ids
    entries2 = fetch_liveauctioneers_sitemap_entries(
        "https://www.liveauctioneers.com/price-result-sitemap-index.xml.gz",
        fetch_bytes=fetch_bytes,
        max_sitemaps=1,
        min_urls=1,
        sitemap_progress_path=progress_path,
        max_retries=1,
    )
    assert {e.entity_id for e in entries2} == {"second-lot"}
    assert load_lot_cache_ids(cache) == {
        "oil-painting-123",
        "bronze-sculpture-abc",
        "second-lot",
    }


def test_fetch_stops_when_min_urls_reached(tmp_path: Path) -> None:
    index_xml = (FIXTURES / "la_price_result_index.xml").read_bytes()
    child_xml = (FIXTURES / "la_price_result_child.xml").read_bytes()
    calls: list[str] = []
    progress_path = tmp_path / "progress.json"

    def fetch_bytes(url: str) -> bytes:
        calls.append(url)
        if "index" in url:
            return index_xml
        return child_xml

    entries = fetch_liveauctioneers_sitemap_entries(
        "https://www.liveauctioneers.com/price-result-sitemap-index.xml.gz",
        fetch_bytes=fetch_bytes,
        max_sitemaps=10,
        min_urls=2,
        sitemap_progress_path=progress_path,
        max_retries=1,
    )
    assert len(entries) >= 2
    # First child only after index (min_urls met; do not fetch sitemap-2)
    child_calls = [
        u for u in calls if "price-result-sitemap-" in u and "index" not in u
    ]
    assert child_calls == [
        "https://www.liveauctioneers.com/price-result-sitemap-1.xml.gz"
    ]
    # Cache retained even though we stopped early
    assert len(load_lot_cache_ids(lot_cache_path(progress_path))) >= 2


def test_sitemap_force_clears_progress_and_cache(tmp_path: Path) -> None:
    index_xml = (FIXTURES / "la_price_result_index.xml").read_bytes()
    child_xml = (FIXTURES / "la_price_result_child.xml").read_bytes()
    progress_path = auction_sitemap_progress_file(tmp_path, "liveauctioneers")
    cache = lot_cache_path(progress_path)

    def fetch_bytes(url: str) -> bytes:
        if "index" in url:
            return index_xml
        return child_xml

    fetch_liveauctioneers_sitemap_entries(
        "https://www.liveauctioneers.com/price-result-sitemap-index.xml.gz",
        fetch_bytes=fetch_bytes,
        max_sitemaps=1,
        min_urls=1,
        sitemap_progress_path=progress_path,
        max_retries=1,
    )
    assert progress_path.is_file()
    assert cache.is_file()

    clear_sitemap_cache(progress_path=progress_path)
    assert not progress_path.exists()
    assert not cache.exists()

    entries = fetch_liveauctioneers_sitemap_entries(
        "https://www.liveauctioneers.com/price-result-sitemap-index.xml.gz",
        fetch_bytes=fetch_bytes,
        max_sitemaps=1,
        min_urls=1,
        sitemap_progress_path=progress_path,
        sitemap_force=True,
        max_retries=1,
    )
    assert len(entries) == 2
    assert load_lot_cache_ids(cache) == {
        "oil-painting-123",
        "bronze-sculpture-abc",
    }


def test_reclaim_done_children_for_cache_backfill() -> None:
    from html_downloader.auctions.liveauctioneers import (
        reclaim_done_children_for_cache_backfill,
    )

    progress = {
        "https://www.liveauctioneers.com/price-result-sitemap-1.xml.gz": {
            "status": "done",
            "lots_found": 1000,
        },
        "https://www.liveauctioneers.com/price-result-sitemap-2.xml.gz": {
            "status": "done",
            "lots_found": 2000,
        },
        "https://www.liveauctioneers.com/price-result-sitemap-3.xml.gz": {
            "status": "pending",
            "lots_found": 0,
        },
    }
    # Cache empty vs 3000 done lots → reclaim done children
    assert reclaim_done_children_for_cache_backfill(progress, cached_ids=0) == 2
    assert (
        progress["https://www.liveauctioneers.com/price-result-sitemap-1.xml.gz"][
            "status"
        ]
        == "pending"
    )
    assert (
        progress["https://www.liveauctioneers.com/price-result-sitemap-3.xml.gz"][
            "status"
        ]
        == "pending"
    )

    # Cache already covers most done lots → no reclaim
    progress2 = {
        "https://www.liveauctioneers.com/a.xml.gz": {
            "status": "done",
            "lots_found": 100,
        }
    }
    assert reclaim_done_children_for_cache_backfill(progress2, cached_ids=80) == 0
    assert progress2["https://www.liveauctioneers.com/a.xml.gz"]["status"] == "done"


def test_fetch_backfills_done_children_into_cache(tmp_path: Path) -> None:
    index_xml = (FIXTURES / "la_price_result_index.xml").read_bytes()
    child_xml = (FIXTURES / "la_price_result_child.xml").read_bytes()
    progress_path = auction_sitemap_progress_file(tmp_path, "liveauctioneers")
    # Pretend an older run marked the first child done without writing JSONL.
    save_sitemap_progress(
        progress_path,
        {
            "https://www.liveauctioneers.com/price-result-sitemap-1.xml.gz": {
                "status": "done",
                "lots_found": 2,
            }
        },
    )

    def fetch_bytes(url: str) -> bytes:
        if "index" in url:
            return index_xml
        if "price-result-sitemap-1" in url:
            return child_xml
        return b"""<?xml version="1.0"?>
        <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
          <url><loc>https://www.liveauctioneers.com/price-result/second-lot/</loc></url>
        </urlset>
        """

    entries = fetch_liveauctioneers_sitemap_entries(
        "https://www.liveauctioneers.com/price-result-sitemap-index.xml.gz",
        fetch_bytes=fetch_bytes,
        max_sitemaps=2,
        min_urls=10,
        sitemap_progress_path=progress_path,
        max_retries=1,
    )
    ids = {e.entity_id for e in entries}
    assert "oil-painting-123" in ids
    assert "bronze-sculpture-abc" in ids
    assert "second-lot" in ids
    assert load_lot_cache_ids(lot_cache_path(progress_path)) == {
        "oil-painting-123",
        "bronze-sculpture-abc",
        "second-lot",
    }


def test_fetch_skips_ids_already_in_cache(tmp_path: Path) -> None:
    index_xml = (FIXTURES / "la_price_result_index.xml").read_bytes()
    child_xml = (FIXTURES / "la_price_result_child.xml").read_bytes()
    progress_path = auction_sitemap_progress_file(tmp_path, "liveauctioneers")
    cache = lot_cache_path(progress_path)
    append_lot_cache(
        cache,
        [
            SitemapEntry(
                url="https://www.liveauctioneers.com/price-result/oil-painting-123",
                lastmod="2026-01-01",
                entity_type="price_result",
                entity_id="oil-painting-123",
            )
        ],
    )

    def fetch_bytes(url: str) -> bytes:
        if "index" in url:
            return index_xml
        return child_xml

    entries = fetch_liveauctioneers_sitemap_entries(
        "https://www.liveauctioneers.com/price-result-sitemap-index.xml.gz",
        fetch_bytes=fetch_bytes,
        max_sitemaps=1,
        min_urls=10,
        sitemap_progress_path=progress_path,
        max_retries=1,
    )
    assert {e.entity_id for e in entries} == {"bronze-sculpture-abc"}
    rows = list(iter_lot_cache_rows(cache))
    assert len(rows) == 2
    assert json.loads(cache.read_text(encoding="utf-8").splitlines()[0])[
        "entity_id"
    ] == "oil-painting-123"

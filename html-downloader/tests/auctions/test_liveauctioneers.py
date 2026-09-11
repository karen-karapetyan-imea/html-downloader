from __future__ import annotations

from pathlib import Path

from html_downloader.auctions.liveauctioneers import (
    fetch_liveauctioneers_sitemap_entries,
    is_liveauctioneers_price_result_sitemap,
    load_sitemap_progress,
    parse_liveauctioneers_entries,
    save_sitemap_progress,
    select_child_sitemaps,
)
from html_downloader.auctions.paths import auction_sitemap_progress_file

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


def test_fetch_expands_index_with_max_sitemaps(tmp_path: Path) -> None:
    index_xml = (FIXTURES / "la_price_result_index.xml").read_bytes()
    child_xml = (FIXTURES / "la_price_result_child.xml").read_bytes()
    progress_path = auction_sitemap_progress_file(tmp_path, "liveauctioneers")

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

    # Second run expands the next pending child
    entries2 = fetch_liveauctioneers_sitemap_entries(
        "https://www.liveauctioneers.com/price-result-sitemap-index.xml.gz",
        fetch_bytes=fetch_bytes,
        max_sitemaps=1,
        min_urls=1,
        sitemap_progress_path=progress_path,
        max_retries=1,
    )
    assert {e.entity_id for e in entries2} == {"second-lot"}


def test_fetch_stops_when_min_urls_reached(tmp_path: Path) -> None:
    index_xml = (FIXTURES / "la_price_result_index.xml").read_bytes()
    child_xml = (FIXTURES / "la_price_result_child.xml").read_bytes()
    calls: list[str] = []

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
        sitemap_progress_path=tmp_path / "progress.json",
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

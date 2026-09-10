from __future__ import annotations

from pathlib import Path

from html_downloader.auctions.invaluable_artist_sold import (
    artist_sold_page_url,
    coverage_overlap_report,
    crawl_artist_sold_pages,
    expand_lots_from_artist_sold,
    extract_lot_entries_from_html,
    load_artist_sold_lot_entries,
    load_artist_sold_progress,
    merge_artist_sold_seeds,
    normalize_artist_sold_url,
    parse_artist_sold_seed_urls,
    sold_seed_from_artist_profile_url,
)


def test_parse_artist_sold_seed_urls() -> None:
    xml = b"""<?xml version="1.0"?>
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url><loc>https://www.invaluable.com/artist/picasso-abc123/sold-at-auction-prices/</loc></url>
      <url><loc>https://www.invaluable.com/artist/monet-def456/sold-at-auction-prices</loc></url>
      <url><loc>https://www.invaluable.com/artist/picasso-abc123/sold-at-auction-prices/</loc></url>
      <url><loc>https://www.invaluable.com/artist/ignored/profile/</loc></url>
    </urlset>
    """
    seeds = parse_artist_sold_seed_urls(xml)
    assert seeds == [
        "https://www.invaluable.com/artist/picasso-abc123/sold-at-auction-prices",
        "https://www.invaluable.com/artist/monet-def456/sold-at-auction-prices",
    ]


def test_normalize_and_page_url() -> None:
    assert (
        normalize_artist_sold_url(
            "https://www.invaluable.com/artist/x-1/sold-at-auction-prices/"
        )
        == "https://www.invaluable.com/artist/x-1/sold-at-auction-prices"
    )
    assert artist_sold_page_url(
        "https://www.invaluable.com/artist/x-1/sold-at-auction-prices", 1
    ).endswith("/sold-at-auction-prices/")
    assert (
        artist_sold_page_url(
            "https://www.invaluable.com/artist/x-1/sold-at-auction-prices", 3
        )
        == "https://www.invaluable.com/artist/x-1/sold-at-auction-prices/?page=3"
    )


def test_sold_seed_from_profile_and_gap_fill() -> None:
    assert (
        sold_seed_from_artist_profile_url(
            "https://www.invaluable.com/artist/dali-salvador-9chkguv69j/"
        )
        == "https://www.invaluable.com/artist/dali-salvador-9chkguv69j/sold-at-auction-prices"
    )
    merged = merge_artist_sold_seeds(
        ["https://www.invaluable.com/artist/a-aaaaaa/sold-at-auction-prices"],
        [
            "https://www.invaluable.com/artist/a-aaaaaa",
            "https://www.invaluable.com/artist/b-bbbbbb",
        ],
    )
    assert merged == [
        "https://www.invaluable.com/artist/a-aaaaaa/sold-at-auction-prices",
        "https://www.invaluable.com/artist/b-bbbbbb/sold-at-auction-prices",
    ]


def test_coverage_overlap_report() -> None:
    report = coverage_overlap_report({"a", "b"}, {"b", "c"})
    assert report == {
        "xml_lots": 2,
        "artist_sold_lots": 2,
        "overlap": 1,
        "xml_only": 1,
        "artist_sold_only": 1,
    }


def test_extract_lots_from_html() -> None:
    html = """
    <a href="/auction-lot/sunset-c-aaaaaaaaaa">lot</a>
    <meta content="https://www.invaluable.com/auction-lot/moon-c-bbbbbbbbbb" />
    <a href="/catalog/0ak1fxhm3a">not a lot</a>
    """
    entries = extract_lot_entries_from_html(
        html, base_url="https://www.invaluable.com/artist/x/sold-at-auction-prices/"
    )
    keys = {e.entity_key for e in entries}
    assert keys == {("lot", "aaaaaaaaaa"), ("lot", "bbbbbbbbbb")}


def test_crawl_stops_on_empty_pages() -> None:
    pages = {
        1: '<a href="/auction-lot/a-c-1111111111">a</a>',
        2: '<a href="/auction-lot/b-c-2222222222">b</a>',
        3: "<html>no lots</html>",
        4: "<html>still none</html>",
    }

    def fetch(url: str) -> str:
        if "page=" not in url:
            return pages[1]
        page = int(url.rsplit("page=", 1)[-1])
        return pages[page]

    entries = crawl_artist_sold_pages(
        "https://www.invaluable.com/artist/x-1/sold-at-auction-prices",
        fetch_html=fetch,
        page_sleep=0,
        page_fetch_retries=1,
    )
    assert {e.entity_id for e in entries} == {"1111111111", "2222222222"}


def test_crawl_retries_transient_failures() -> None:
    calls = {"n": 0}

    def fetch(_url: str) -> str:
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("html fetch status=403")
        return '<a href="/auction-lot/a-c-1111111111">a</a>'

    entries = crawl_artist_sold_pages(
        "https://www.invaluable.com/artist/x-1/sold-at-auction-prices",
        fetch_html=fetch,
        page_sleep=0,
        page_fetch_retries=3,
        empty_streak_limit=1,
        max_pages=1,
    )
    assert {e.entity_id for e in entries} == {"1111111111"}
    assert calls["n"] == 3


def test_expand_resumes_and_keeps_lots(tmp_path: Path) -> None:
    progress = tmp_path / "progress.json"
    seed_a = "https://www.invaluable.com/artist/a-1/sold-at-auction-prices"
    seed_b = "https://www.invaluable.com/artist/b-2/sold-at-auction-prices"

    def fetch_a(_url: str) -> str:
        return '<a href="/auction-lot/old-c-aaaaaaaaaa">a</a>'

    first = expand_lots_from_artist_sold(
        [seed_a, seed_b],
        fetch_html=fetch_a,
        progress_path=progress,
        concurrency=1,
        max_artists=1,
        artist_sleep=0,
        max_pages_per_artist=1,
    )
    assert len(first) == 1
    assert load_artist_sold_progress(progress) == {seed_a}
    assert len(load_artist_sold_lot_entries(progress)) == 1

    def fetch_b(url: str) -> str:
        if "b-2" in url:
            return '<a href="/auction-lot/new-c-bbbbbbbbbb">b</a>'
        raise AssertionError(f"should not refetch completed seed: {url}")

    second = expand_lots_from_artist_sold(
        [seed_a, seed_b],
        fetch_html=fetch_b,
        progress_path=progress,
        concurrency=1,
        artist_sleep=0,
        max_pages_per_artist=1,
    )
    ids = {e.entity_id for e in second}
    assert ids == {"aaaaaaaaaa", "bbbbbbbbbb"}
    assert load_artist_sold_progress(progress) == {seed_a, seed_b}

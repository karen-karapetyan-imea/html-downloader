from __future__ import annotations

import json
from pathlib import Path

import pytest

from html_downloader.auctions.christies import (
    classify_child_sitemap,
    collect_christies_sales,
    fetch_christies_entries,
    fetch_christies_sitemap_entries,
    is_christies_auction_sitemap,
    is_christies_lot_sitemap,
    iter_lot_cache_rows,
    lot_cache_path,
    parse_christies_entries,
)
from html_downloader.auctions.sitemap_cache import TypedKeySet

INDEX_URL = "https://www.christies.com/sitemap/sitemap_index.xml"
LOT_PAST_1 = "https://www.christies.com/sitemap/lot_past_1_sitemap.xml"
LOT_PAST_2 = "https://www.christies.com/sitemap/lot_past_2_sitemap.xml"
AUCTION_PAST = "https://www.christies.com/sitemap/auction_past_1_sitemap.xml"
AUCTION_UPCOMING = "https://www.christies.com/sitemap/auction_upcoming_1_sitemap.xml"


def _index(lastmod: str) -> bytes:
    children = [
        "https://www.christies.com/sitemap/stories_sitemap.xml",
        "https://www.christies.com/sitemap-image.xml",
        LOT_PAST_2,
        LOT_PAST_1,
        AUCTION_PAST,
        AUCTION_UPCOMING,
    ]
    body = "".join(
        f"<sitemap><loc>{url}</loc><lastmod>{lastmod}</lastmod></sitemap>" for url in children
    )
    return (
        '<?xml version="1.0"?><sitemapindex '
        'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"{body}</sitemapindex>"
    ).encode()


def _urlset(*locs: tuple[str, str]) -> bytes:
    body = "".join(f"<url><loc>{loc}</loc><lastmod>{lm}</lastmod></url>" for loc, lm in locs)
    return (
        '<?xml version="1.0"?><urlset '
        'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"{body}</urlset>"
    ).encode()


LOTS_1 = _urlset(
    ("https://www.christies.com/en/lot/lot-300", "2026-07-21"),
    ("https://www.christies.com/zh/lot/lot-300", "2026-07-21"),
    ("https://www.christies.com/zh-cn/lot/lot-300", "2026-07-21"),
    ("https://www.christies.com/en/lot/lot-299", "2026-07-20"),
)
LOTS_2 = _urlset(
    ("https://www.christies.com/en/lot/lot-200", "2025-11-19"),
    ("https://www.christies.com/en/stories/not-a-lot", "2025-11-19"),
)
AUCTIONS = _urlset(
    ("https://www.christies.com/en/auction/auction-24211-par", "2025-11-19"),
    ("https://www.christies.com/zh/auction/auction-24211-par", "2025-11-19"),
    ("https://www.christies.com/en/auction/auction-7503-csk", "2008-03-22"),
)
UPCOMING = _urlset(("https://www.christies.com/en/auction/auction-24999-nyr", "2026-10-01"))


class FakeFetcher:
    def __init__(self, responses: dict[str, bytes], fail: set[str] | None = None) -> None:
        self.responses = responses
        self.fail = fail or set()
        self.calls: list[str] = []

    def __call__(self, url: str) -> bytes:
        self.calls.append(url)
        if url in self.fail:
            raise RuntimeError(f"sitemap fetch status=404 url={url}")
        return self.responses[url]


def _responses(index_lastmod: str = "2026-09-01") -> dict[str, bytes]:
    return {
        INDEX_URL: _index(index_lastmod),
        LOT_PAST_1: LOTS_1,
        LOT_PAST_2: LOTS_2,
        AUCTION_PAST: AUCTIONS,
        AUCTION_UPCOMING: UPCOMING,
    }


def test_child_sitemap_classification() -> None:
    assert is_christies_lot_sitemap(LOT_PAST_1)
    assert is_christies_lot_sitemap("https://www.christies.com/sitemap/lot_upcoming_1_sitemap.xml")
    assert is_christies_auction_sitemap(AUCTION_PAST)
    assert not is_christies_lot_sitemap("https://www.christies.com/sitemap/stories_sitemap.xml")
    assert classify_child_sitemap("https://www.christies.com/sitemap-video.xml") is None
    child = classify_child_sitemap(LOT_PAST_2, "2026-09-01")
    assert child is not None
    assert (child.kind, child.phase, child.number, child.lastmod) == ("lot", "past", 2, "2026-09-01")


def test_parse_entries_collapses_locales_and_skips_non_entities() -> None:
    lots = parse_christies_entries(LOTS_1)
    assert sorted(e.url for e in lots) == [
        "https://www.christies.com/en/lot/lot-299",
        "https://www.christies.com/en/lot/lot-300",
    ]
    assert {e.entity_key for e in parse_christies_entries(LOTS_2)} == {("lot", "200")}
    sales = parse_christies_entries(AUCTIONS)
    assert {e.entity_key for e in sales} == {("sale", "24211-par"), ("sale", "7503-csk")}


def test_parse_entries_rejects_html_block_page() -> None:
    with pytest.raises(RuntimeError):
        parse_christies_entries(b"<html><title>Access Denied</title></html>")


def test_full_walk_writes_cache_and_resumes_within_month(tmp_path: Path) -> None:
    progress = tmp_path / "christies_sitemap_progress.json"
    fetch = FakeFetcher(_responses())

    first = fetch_christies_sitemap_entries(
        INDEX_URL, fetch_bytes=fetch, sitemap_progress_path=progress, inter_file_sleep=0
    )
    assert {e.entity_key for e in first} == {
        ("lot", "300"),
        ("lot", "299"),
        ("lot", "200"),
        ("sale", "24211-par"),
        ("sale", "7503-csk"),
        ("sale", "24999-nyr"),
    }
    # Sale maps first, then past lots in file-number order.
    assert fetch.calls[1:] == [AUCTION_UPCOMING, AUCTION_PAST, LOT_PAST_1, LOT_PAST_2]
    rows = list(iter_lot_cache_rows(lot_cache_path(progress)))
    assert len(rows) == 6

    state = json.loads(progress.read_text(encoding="utf-8"))["sitemaps"]
    assert state[LOT_PAST_1]["status"] == "done"
    assert state[LOT_PAST_1]["epoch"] == "2026-09-01"

    fetch_again = FakeFetcher(_responses())
    second = fetch_christies_sitemap_entries(
        INDEX_URL, fetch_bytes=fetch_again, sitemap_progress_path=progress, inter_file_sleep=0
    )
    assert second == []
    assert fetch_again.calls == [INDEX_URL]


def test_new_index_lastmod_rewalks_and_dedupes(tmp_path: Path) -> None:
    progress = tmp_path / "christies_sitemap_progress.json"
    fetch_christies_sitemap_entries(
        INDEX_URL, fetch_bytes=FakeFetcher(_responses()), sitemap_progress_path=progress,
        inter_file_sleep=0,
    )
    responses = _responses("2026-10-01")
    responses[LOT_PAST_1] = _urlset(
        ("https://www.christies.com/en/lot/lot-301", "2026-09-30"),
        ("https://www.christies.com/en/lot/lot-300", "2026-07-21"),
    )
    fetch = FakeFetcher(responses)
    new = fetch_christies_sitemap_entries(
        INDEX_URL, fetch_bytes=fetch, sitemap_progress_path=progress, inter_file_sleep=0
    )
    assert [e.entity_key for e in new] == [("lot", "301")]
    assert len(fetch.calls) == 5
    assert len(list(iter_lot_cache_rows(lot_cache_path(progress)))) == 7


def test_failed_child_is_retried_next_run(tmp_path: Path) -> None:
    progress = tmp_path / "christies_sitemap_progress.json"
    fetch_christies_sitemap_entries(
        INDEX_URL,
        fetch_bytes=FakeFetcher(_responses(), fail={LOT_PAST_2}),
        sitemap_progress_path=progress,
        inter_file_sleep=0,
        max_retries=1,
    )
    state = json.loads(progress.read_text(encoding="utf-8"))["sitemaps"]
    assert state[LOT_PAST_2]["status"] == "failed"

    fetch = FakeFetcher(_responses())
    new = fetch_christies_sitemap_entries(
        INDEX_URL, fetch_bytes=fetch, sitemap_progress_path=progress, inter_file_sleep=0
    )
    assert fetch.calls == [INDEX_URL, LOT_PAST_2]
    assert [e.entity_key for e in new] == [("lot", "200")]


def test_all_children_failed_with_empty_cache_raises(tmp_path: Path) -> None:
    fail = {LOT_PAST_1, LOT_PAST_2, AUCTION_PAST, AUCTION_UPCOMING}
    with pytest.raises(RuntimeError):
        fetch_christies_sitemap_entries(
            INDEX_URL,
            fetch_bytes=FakeFetcher(_responses(), fail=fail),
            sitemap_progress_path=tmp_path / "p.json",
            inter_file_sleep=0,
            max_retries=1,
        )


def test_max_sitemaps_limits_children(tmp_path: Path) -> None:
    fetch = FakeFetcher(_responses())
    fetch_christies_sitemap_entries(
        INDEX_URL,
        fetch_bytes=fetch,
        sitemap_progress_path=tmp_path / "p.json",
        max_sitemaps=2,
        inter_file_sleep=0,
    )
    assert fetch.calls == [INDEX_URL, AUCTION_UPCOMING, AUCTION_PAST]


def test_collect_sales_past_only_newest_first() -> None:
    sales = collect_christies_sales(INDEX_URL, fetch_bytes=FakeFetcher(_responses()))
    assert [(s.sale_number, s.sale_room_code, s.lastmod) for s in sales] == [
        ("24211", "PAR", "2025-11-19"),
        ("7503", "CSK", "2008-03-22"),
    ]
    assert sales[0].key == "24211-par"
    with_upcoming = collect_christies_sales(
        INDEX_URL, fetch_bytes=FakeFetcher(_responses()), include_upcoming=True
    )
    assert with_upcoming[0].key == "24999-nyr"


def test_dispatch_art_mode_requires_state_path() -> None:
    with pytest.raises(ValueError):
        fetch_christies_entries(art_categories=["Paintings"], art_state_path=None)


def test_typed_key_set_compacts_numeric_ids() -> None:
    keys = TypedKeySet()
    assert keys.add(("lot", "6557734"))
    assert not keys.add(("lot", "6557734"))
    assert keys.add(("sale", "24211-par"))
    assert keys.add(("lot", "007"))  # leading zero stays a distinct string id
    assert ("lot", "6557734") in keys
    assert ("lot", "7") not in keys
    assert len(keys) == 3
    assert keys.count("lot") == 2

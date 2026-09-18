"""Tests for Drouot sitemap + search discovery."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from html_downloader.auctions.drouot import (
    fetch_drouot_sitemap_entries,
    is_drouot_lot_sitemap,
    is_drouot_sale_sitemap,
    load_lot_cache_keys,
    lot_cache_path,
    parse_drouot_entries,
)
from html_downloader.auctions.drouot_search import (
    decode_search_payload,
    expand_lots_from_search,
    probe_search_total,
    search_data_url,
)
from html_downloader.discover.sitemap import SitemapEntry

LOT_URLSET = b"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>https://drouot.com/fr/l/34624465-Tiffany-Heart</loc>
    <lastmod>2026-09-17T20:00:00Z</lastmod>
  </url>
  <url>
    <loc>https://www.drouot.com/en/v/184832-japanese-crafts</loc>
    <lastmod>2026-09-17T20:01:00Z</lastmod>
  </url>
  <url>
    <loc>https://drouot.com/en/s?query=painting</loc>
  </url>
</urlset>
"""

LOT_INDEX = b"""<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap>
    <loc>https://drouot.com/sitemap-en-lot1.xml</loc>
  </sitemap>
</sitemapindex>
"""

SALE_INDEX = b"""<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap>
    <loc>https://drouot.com/sitemap-en-sale1.xml</loc>
  </sitemap>
</sitemapindex>
"""


def _search_payload(*, total_items: int, total_pages: int, lots: list[tuple[int, str]]) -> dict:
    """Build a minimal SvelteKit-style search payload with numbered refs."""
    # Layout: [head, lots_list, ...lot_dicts..., ...scalars...]
    data: list[object] = []
    head = {
        "lots": 1,
        "searchId": 2,
        "totalItems": 3,
        "totalPages": 4,
    }
    data.append(head)
    # placeholders filled after we know indices
    data.extend([None, None, None, None])  # 1..4

    lot_refs: list[int] = []
    for lot_id, slug in lots:
        lot_obj = {
            "id": len(data) + 1,
            "slug": len(data) + 2,
        }
        # We'll append lot_obj then id then slug
        lot_idx = len(data)
        data.append(lot_obj)  # temporary; fix refs after append id/slug
        data.append(lot_id)
        data.append(slug)
        data[lot_idx] = {"id": lot_idx + 1, "slug": lot_idx + 2}
        lot_refs.append(lot_idx)

    data[1] = lot_refs
    data[2] = "search-id"
    data[3] = total_items
    data[4] = total_pages
    return {"type": "data", "nodes": [{"type": "data", "data": data}]}


def test_is_drouot_child_sitemap_filters() -> None:
    assert is_drouot_lot_sitemap("https://drouot.com/sitemap-en-lot1.xml")
    assert is_drouot_lot_sitemap("https://drouot.com/sitemap-en-lot5.xml")
    assert not is_drouot_lot_sitemap("https://drouot.com/sitemap-en-lot.xml")
    assert is_drouot_sale_sitemap("https://drouot.com/sitemap-en-sale1.xml")
    assert not is_drouot_sale_sitemap("https://drouot.com/sitemap-en-sale.xml")


def test_parse_drouot_entries_normalizes_locale_and_host() -> None:
    entries = parse_drouot_entries(LOT_URLSET)
    by_key = {e.entity_key: e for e in entries}
    assert ("lot", "34624465") in by_key
    assert by_key[("lot", "34624465")].url == (
        "https://drouot.com/en/l/34624465-tiffany-heart"
    )
    assert ("sale", "184832") in by_key
    assert by_key[("sale", "184832")].url == (
        "https://drouot.com/en/v/184832-japanese-crafts"
    )
    assert len(entries) == 2


def test_decode_search_payload_and_probe() -> None:
    payload = _search_payload(
        total_items=3,
        total_pages=1,
        lots=[(1001, "silver-coin"), (1002, "oil-painting")],
    )
    total, pages, entries = decode_search_payload(payload)
    assert total == 3
    assert pages == 1
    assert len(entries) == 2
    assert entries[0].url == "https://drouot.com/en/l/1001-silver-coin"
    assert entries[0].entity_key == ("lot", "1001")

    assert search_data_url(query="*", page=2).endswith("query=%2A&page=2")

    def fake_fetch(url: str) -> dict:
        return payload

    assert probe_search_total(fetch_json=fake_fetch) == 3


def test_expand_lots_from_search_resume(tmp_path: Path) -> None:
    state_path = tmp_path / "drouot_algolia_browse_state.json"
    cache = tmp_path / "drouot_sitemap_progress_lots.jsonl"

    pages = {
        1: _search_payload(
            total_items=3,
            total_pages=2,
            lots=[(1, "a"), (2, "b")],
        ),
        2: _search_payload(
            total_items=3,
            total_pages=2,
            lots=[(3, "c")],
        ),
    }

    def fake_fetch(url: str) -> dict:
        if "page=2" in url:
            return pages[2]
        return pages[1]

    first = expand_lots_from_search(
        state_path=state_path,
        cache_path=cache,
        fetch_json=fake_fetch,
        delay=0,
    )
    assert {e.entity_id for e in first} == {"1", "2", "3"}
    keys = load_lot_cache_keys(cache)
    assert keys == {("lot", "1"), ("lot", "2"), ("lot", "3")}

    second = expand_lots_from_search(
        state_path=state_path,
        cache_path=cache,
        fetch_json=fake_fetch,
        delay=0,
    )
    assert second == []


def test_fetch_drouot_triggers_search_when_count_low(tmp_path: Path) -> None:
    progress = tmp_path / "drouot_sitemap_progress.json"
    state = tmp_path / "drouot_algolia_browse_state.json"

    bodies = {
        "https://drouot.com/sitemap-en-lot.xml": LOT_INDEX,
        "https://drouot.com/sitemap-en-sale.xml": SALE_INDEX,
        "https://drouot.com/sitemap-en-lot1.xml": LOT_URLSET,
        "https://drouot.com/sitemap-en-sale1.xml": b"""<?xml version="1.0"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://drouot.com/en/v/999-extra-sale</loc></url>
</urlset>
""",
    }

    search_calls: list[str] = []

    def fake_probe() -> int:
        search_calls.append("probe")
        return 10  # higher than sitemap lot count (1)

    def fake_expand(**kwargs: object) -> list[SitemapEntry]:
        search_calls.append("expand")
        return [
            SitemapEntry(
                url="https://drouot.com/en/l/777-from-search",
                lastmod=None,
                entity_type="lot",
                entity_id="777",
            )
        ]

    import html_downloader.auctions.drouot_search as search_mod

    with (
        patch.object(search_mod, "probe_search_total", side_effect=fake_probe),
        patch.object(search_mod, "expand_lots_from_search", side_effect=fake_expand),
    ):
        entries = fetch_drouot_sitemap_entries(
            fetch_bytes=lambda u: bodies[u],
            sitemap_progress_path=progress,
            expand_algolia=True,
            algolia_state_path=state,
            max_sitemaps=10,
            min_urls=0,
        )

    assert search_calls == ["probe", "expand"]
    assert any(e.entity_id == "34624465" for e in entries)
    assert any(e.entity_id == "777" for e in entries)
    cache_keys = load_lot_cache_keys(lot_cache_path(progress))
    assert ("lot", "34624465") in cache_keys
    assert ("sale", "184832") in cache_keys or ("sale", "999") in cache_keys


def test_fetch_skips_search_when_count_sufficient(tmp_path: Path) -> None:
    progress = tmp_path / "drouot_sitemap_progress.json"
    state = tmp_path / "drouot_algolia_browse_state.json"
    bodies = {
        "https://drouot.com/sitemap-en-lot.xml": LOT_INDEX,
        "https://drouot.com/sitemap-en-sale.xml": SALE_INDEX,
        "https://drouot.com/sitemap-en-lot1.xml": LOT_URLSET,
        "https://drouot.com/sitemap-en-sale1.xml": b"""<?xml version="1.0"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"></urlset>
""",
    }

    import html_downloader.auctions.drouot_search as search_mod

    probe_calls: list[int] = []

    def fake_probe() -> int:
        probe_calls.append(1)
        return 1  # equal to one lot in urlset

    with (
        patch.object(search_mod, "probe_search_total", side_effect=fake_probe),
        patch.object(search_mod, "expand_lots_from_search") as expand_mock,
    ):
        fetch_drouot_sitemap_entries(
            fetch_bytes=lambda u: bodies[u],
            sitemap_progress_path=progress,
            expand_algolia=True,
            algolia_state_path=state,
        )
        expand_mock.assert_not_called()
    assert probe_calls == [1]

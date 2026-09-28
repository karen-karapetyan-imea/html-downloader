from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from html_downloader.auctions.christies import ChristiesSale
from html_downloader.auctions.christies_search import (
    ART_CATEGORY_FACETS,
    PAGE_SIZE,
    LotSearchClient,
    build_filterids,
    expand_lots_from_lotsearch,
    facet_filter_id,
    lot_cache_path,
    lot_to_entry,
    search_sale,
)
from html_downloader.auctions.sitemap_cache import iter_typed_cache_rows

SALE_A = ChristiesSale("24211", "PAR", "2025-11-19")
SALE_B = ChristiesSale("24496", "NYR", "2026-09-24")


def _lot(object_id: int | str, url: str | None = None) -> dict:
    return {
        "object_id": str(object_id),
        "url": url or f"https://www.christies.com/en/lot/lot-{object_id}?ldp_breadcrumb=back",
        "end_date": "2025-11-18T23:00Z",
    }


def test_facet_filter_id_encodings() -> None:
    assert facet_filter_id("Paintings") == "CoaCategories{Paintings}"
    assert facet_filter_id("Prints & Multiples") == "CoaCategories{Prints+%26+Multiples}"
    assert facet_filter_id("Prints+%26+Multiples") == "CoaCategories{Prints+%26+Multiples}"
    assert facet_filter_id("Sculptures, Statues & Figures").lower() == (
        "coacategories{sculptures%2c+statues+%26+figures}"
    )
    assert facet_filter_id("CoaCategories{Watches}") == "CoaCategories{Watches}"
    with pytest.raises(ValueError):
        facet_filter_id("  ")


def test_build_filterids_ors_and_dedupes() -> None:
    assert build_filterids(["Paintings", "Photographs", "Paintings"]) == (
        "CoaCategories{Paintings}|CoaCategories{Photographs}"
    )
    assert build_filterids(ART_CATEGORY_FACETS).count("|") == len(ART_CATEGORY_FACETS) - 1
    with pytest.raises(ValueError):
        build_filterids([])


def test_lot_to_entry_maps_online_sso_url_to_canonical() -> None:
    entry = lot_to_entry(
        _lot(6599499, "https://www.christies.com/en/sso?ObjectID=24496.1&LotNumber=1"),
        SALE_B,
    )
    assert entry is not None
    assert entry.url == "https://www.christies.com/en/lot/lot-6599499"
    assert entry.entity_key == ("lot", "6599499")
    assert entry.lastmod == "2025-11-18"
    assert lot_to_entry({"object_id": "24496.1"}, SALE_B) is None
    no_date = lot_to_entry({"object_id": "5"}, SALE_A)
    assert no_date is not None and no_date.lastmod == "2025-11-19"


def test_search_sale_paginates_until_total() -> None:
    pages = {
        1: {"lots": [_lot(i) for i in range(PAGE_SIZE)], "total_hits_filtered": PAGE_SIZE + 2},
        2: {"lots": [_lot(1000), _lot(1001)], "total_hits_filtered": PAGE_SIZE + 2},
    }
    seen_pages: list[int] = []

    def search(sale: ChristiesSale, filterids: str, page: int) -> dict:
        seen_pages.append(page)
        return pages[page]

    result = search_sale(SALE_A, filterids="x", search=search)
    assert result is not None
    entries, total = result
    assert total == PAGE_SIZE + 2
    assert len(entries) == PAGE_SIZE + 2
    assert seen_pages == [1, 2]


def test_search_sale_failure_returns_none() -> None:
    assert search_sale(SALE_A, filterids="x", search=lambda *_: None) is None


def test_expand_writes_cache_skips_done_and_retries_failed(tmp_path: Path) -> None:
    state_path = tmp_path / "christies_algolia_artworks_browse_state.json"
    calls: list[str] = []
    fail_b = {"on": True}

    def search(sale: ChristiesSale, filterids: str, page: int) -> dict | None:
        calls.append(sale.key)
        assert filterids == build_filterids(["Paintings"])
        if sale.key == SALE_B.key:
            if fail_b["on"]:
                return None
            return {"lots": [_lot(20), _lot(10)], "total_hits_filtered": 2}
        return {"lots": [_lot(10), _lot(11)], "total_hits_filtered": 2}

    first = expand_lots_from_lotsearch(
        state_path=state_path,
        sales=[SALE_A, SALE_B],
        categories=["Paintings"],
        workers=1,
        search=search,
    )
    assert {e.entity_id for e in first} == {"10", "11"}
    state = json.loads(state_path.read_text(encoding="utf-8"))["sales"]
    assert state[SALE_A.key]["status"] == "done"
    assert state[SALE_B.key]["status"] == "failed"

    fail_b["on"] = False
    calls.clear()
    second = expand_lots_from_lotsearch(
        state_path=state_path,
        sales=[SALE_A, SALE_B],
        categories=["Paintings"],
        workers=2,
        search=search,
    )
    assert calls == [SALE_B.key]
    assert [e.entity_id for e in second] == ["20"]
    rows = list(iter_typed_cache_rows(lot_cache_path(state_path)))
    assert sorted(r[1] for r in rows) == ["10", "11", "20"]


def test_expand_zero_hits_is_done_and_max_sales_caps(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"

    def search(sale: ChristiesSale, filterids: str, page: int) -> dict:
        return {"lots": [], "total_hits_filtered": 0}

    out = expand_lots_from_lotsearch(
        state_path=state_path,
        sales=[SALE_A, SALE_B],
        categories=["Paintings"],
        max_sales=1,
        search=search,
    )
    assert out == []
    state = json.loads(state_path.read_text(encoding="utf-8"))["sales"]
    assert list(state) == [SALE_A.key]
    assert state[SALE_A.key] == {"status": "done", "hits": 0, "written": 0}


def test_expand_force_resets_state_and_cache(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"

    def search(sale: ChristiesSale, filterids: str, page: int) -> dict:
        return {"lots": [_lot(1)], "total_hits_filtered": 1}

    expand_lots_from_lotsearch(state_path=state_path, sales=[SALE_A], search=search)
    again = expand_lots_from_lotsearch(
        state_path=state_path, sales=[SALE_A], search=search, force=True
    )
    assert [e.entity_id for e in again] == ["1"]
    assert len(list(iter_typed_cache_rows(lot_cache_path(state_path)))) == 1


def test_client_sends_expected_params_and_backs_off_on_429(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("html_downloader.auctions.christies_search.time.sleep", lambda _s: None)
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(429)
        return httpx.Response(200, json={"lots": [_lot(1)], "total_hits_filtered": 1})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = LotSearchClient(delay=0, client=http)
    data = client.search(SALE_A, build_filterids(["Prints & Multiples", "Paintings"]), 3)
    assert data is not None and data["total_hits_filtered"] == 1
    assert len(requests) == 2
    query = parse_qs(urlsplit(str(requests[-1].url)).query)
    assert query["salenumber"] == ["24211"]
    assert query["saleroomcode"] == ["PAR"]
    assert query["page"] == ["3"]
    assert query["pagesize"] == [str(PAGE_SIZE)]
    # Server receives the facet id with the site's own %26 encoding intact.
    assert query["filterids"] == [
        "CoaCategories{Prints+%26+Multiples}|CoaCategories{Paintings}"
    ]


def test_client_returns_none_on_hard_4xx(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("html_downloader.auctions.christies_search.time.sleep", lambda _s: None)
    http = httpx.Client(transport=httpx.MockTransport(lambda _r: httpx.Response(400)))
    assert LotSearchClient(delay=0, client=http).search(SALE_A, "x", 1) is None

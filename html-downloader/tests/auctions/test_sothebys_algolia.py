from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest

from html_downloader.auctions import sothebys_algolia as sa
from html_downloader.auctions.algolia_partition import PartitionLog, partition_log_path
from html_downloader.auctions.sitemap_cache import iter_typed_cache_rows
from html_downloader.auctions.sothebys_algolia import (
    ART_FACETS,
    AUCTIONS_INDEX,
    GRAPHQL_URL,
    LOTS_INDEX,
    SothebysAlgoliaClient,
    SothebysAuction,
    auction_from_hit,
    build_art_filter,
    expand_lots_from_algolia,
    hit_to_entry,
    list_auctions,
    lot_cache_path,
    lot_filters,
    search_auction_lots,
)
from tests.auctions.algolia_fake import respond

CLOSED = SothebysAuction("aaaa-1", "2025", "old-masters", "Closed", "2025-12-04")
OPEN = SothebysAuction("bbbb-2", "2026", "modern-day-auction-3", "Opened", "2026-09-29")


def _lot(
    auction: SothebysAuction, lot_nr: int, slug: str | None = "", **extra: Any
) -> dict[str, Any]:
    path = (
        f"/en/buy/auction/{auction.slug_year}/{auction.slug_name}/lot-{lot_nr}"
        if slug == ""
        else slug
    )
    return {
        "objectID": f"{auction.auction_id}-{lot_nr}",
        "lotNr": lot_nr,
        "slug": path,
        **extra,
    }


def _auction_hit(auction: SothebysAuction, end_epoch: int) -> dict[str, Any]:
    return {
        "objectID": auction.auction_id,
        "slugYear": auction.slug_year,
        "slugName": auction.slug_name,
        "state": auction.state,
        "auctionDates": {"startDate": end_epoch - 3600, "endDate": end_epoch},
    }


class FakeAlgolia:
    """Both platform indexes; evaluates the real filter strings, per-auction counts exact."""

    def __init__(
        self,
        *,
        auctions: list[dict[str, Any]] | None = None,
        lots: dict[str, list[dict[str, Any]]] | None = None,
    ) -> None:
        self.auctions = auctions or []
        self.lots = lots or {}
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.fail_auctions: set[str] = set()
        self.fail_after: int | None = None

    def __call__(self, index: str, params: dict[str, Any]) -> dict[str, Any] | None:
        self.calls.append((index, params))
        if self.fail_after is not None and len(self.calls) > self.fail_after:
            return None
        filters = params.get("filters", "")
        if index == AUCTIONS_INDEX:
            rows = [{"isTestRecord": False, **a} for a in self.auctions]
            return respond(rows, params)
        if any(f'auctionId:"{aid}"' in filters for aid in self.fail_auctions):
            return None
        rows = [
            {"auctionId": aid, "isTestLot": False, **lot}
            for aid, lots in self.lots.items()
            for lot in lots
        ]
        return respond(rows, params, exact=True)

    def hit_calls(self) -> list[dict[str, Any]]:
        return [p for _, p in self.calls if int(p.get("hitsPerPage", 0)) > 0]


def test_build_art_filter_ors_departments_and_object_types() -> None:
    assert build_art_filter(["Prints", "Painting", "Prints", " "]) == (
        '(departments:"Prints" OR objectTypes:"Prints" '
        'OR departments:"Painting" OR objectTypes:"Painting")'
    )
    assert build_art_filter(['Say "hi"']) == (
        '(departments:"Say \\"hi\\"" OR objectTypes:"Say \\"hi\\"")'
    )
    assert build_art_filter(ART_FACETS).count(" OR ") == 2 * len(ART_FACETS) - 1
    with pytest.raises(ValueError):
        build_art_filter([])


def test_lot_filters_always_exclude_test_lots() -> None:
    assert lot_filters(CLOSED) == 'auctionId:"aaaa-1" AND isTestLot:false'
    assert lot_filters(CLOSED, "(departments:\"Prints\")") == (
        'auctionId:"aaaa-1" AND isTestLot:false AND (departments:"Prints")'
    )


def test_auction_from_hit_parses_dates_and_rejects_bad_rows() -> None:
    auction = auction_from_hit(_auction_hit(CLOSED, 1764806400))
    assert auction == CLOSED
    assert auction is not None and auction.is_closed
    assert auction.url == "https://www.sothebys.com/en/buy/auction/2025/old-masters"
    assert auction.sale_entry().entity_key == ("sale", "2025/old-masters")
    no_dates = auction_from_hit({**_auction_hit(CLOSED, 0), "auctionDates": None})
    assert no_dates is not None and no_dates.end_date is None
    assert auction_from_hit({"objectID": "x", "slugYear": "20", "slugName": "a"}) is None
    assert auction_from_hit({"slugYear": "2025", "slugName": "a"}) is None


def test_hit_to_entry_canonicalizes_and_skips_missing_slug() -> None:
    entry = hit_to_entry(
        _lot(CLOSED, 7, "/zh-hant/buy/auction/2025/old-masters/Some-Lot?x=1"), CLOSED
    )
    assert entry is not None
    assert entry.url == "https://www.sothebys.com/en/buy/auction/2025/old-masters/some-lot"
    assert entry.entity_key == ("lot", "2025/old-masters/some-lot")
    assert entry.lastmod == "2025-12-04"
    assert hit_to_entry(_lot(CLOSED, 1, None), CLOSED) is None
    assert hit_to_entry(_lot(CLOSED, 1, "/en/articles/x"), CLOSED) is None


def test_search_auction_lots_partitions_large_auction(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sa, "MAX_HITS", 3)
    lots = [_lot(CLOSED, n, sessionId=n % 2) for n in range(1, 11)]
    lots.append(_lot(CLOSED, 99, slug="/en/buy/auction/2025/old-masters/no-lot-nr"))
    del lots[-1]["lotNr"]
    fake = FakeAlgolia(lots={CLOSED.auction_id: lots})
    result = search_auction_lots(CLOSED, query=fake)
    assert result is not None
    entries, total = result
    assert total == 11
    assert len(entries) == 11
    assert fake.hit_calls()[0]["filters"] == lot_filters(CLOSED)
    assert len(fake.calls) > 1


def test_expand_marks_unsplittable_auction_incomplete(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(sa, "MAX_HITS", 2)
    lots = [_lot(CLOSED, 5, slug=f"/en/buy/auction/2025/old-masters/x-{i}") for i in range(4)]
    fake = FakeAlgolia(lots={CLOSED.auction_id: lots})
    state_path = tmp_path / "state.json"
    out = expand_lots_from_algolia(state_path=state_path, auctions=[CLOSED], query=fake)
    assert len([e for e in out if e.entity_type == "lot"]) == 2
    entry = json.loads(state_path.read_text(encoding="utf-8"))["sales"][CLOSED.auction_id]
    assert entry["status"] == "incomplete"
    assert entry["incomplete"] == 1 and entry["expected"] == 4 and entry["retrieved"] == 2
    leaves = [
        r
        for r in PartitionLog(partition_log_path(state_path)).latest()[CLOSED.auction_id]
        if r["kind"] == "leaf" and not r["complete"]
    ]
    assert [leaf["reason"] for leaf in leaves] == ["unsplittable"]
    # Incomplete auctions are never ``done``: the next run queries them again.
    fake.calls.clear()
    expand_lots_from_algolia(state_path=state_path, auctions=[CLOSED], query=fake)
    assert fake.calls


def test_expand_collects_auction_over_the_hit_cap(tmp_path: Path) -> None:
    lots = [_lot(CLOSED, n) for n in range(1, 2501)]
    fake = FakeAlgolia(lots={CLOSED.auction_id: lots})
    state_path = tmp_path / "state.json"
    out = expand_lots_from_algolia(state_path=state_path, auctions=[CLOSED], query=fake)
    assert len(out) == 2501  # 2,500 lots + the sale page
    entry = json.loads(state_path.read_text(encoding="utf-8"))["sales"][CLOSED.auction_id]
    assert entry["status"] == "done"
    assert (entry["expected"], entry["retrieved"], entry["urls"]) == (2500, 2500, 2500)
    assert entry["expected_exact"] is True and entry["incomplete"] == 0
    assert entry["partitions"] >= 3 and entry["written"] == 2501
    assert entry["url"] == CLOSED.url


def test_expand_resumes_interrupted_auction(tmp_path: Path) -> None:
    lots = [_lot(CLOSED, n) for n in range(1, 2501)]
    fake = FakeAlgolia(lots={CLOSED.auction_id: lots})
    fake.fail_after = 8
    state_path = tmp_path / "state.json"
    expand_lots_from_algolia(state_path=state_path, auctions=[CLOSED], query=fake)
    assert json.loads(state_path.read_text())["sales"][CLOSED.auction_id]["status"] == "failed"
    written_before = len(list(iter_typed_cache_rows(lot_cache_path(state_path))))
    assert 0 < written_before < 2500

    resumed = FakeAlgolia(lots={CLOSED.auction_id: lots})
    expand_lots_from_algolia(state_path=state_path, auctions=[CLOSED], query=resumed)
    assert lot_filters(CLOSED) not in {p["filters"] for p in resumed.hit_calls()}
    rows = list(iter_typed_cache_rows(lot_cache_path(state_path)))
    assert len(rows) == len({(r[0], r[1]) for r in rows}) == 2501
    entry = json.loads(state_path.read_text())["sales"][CLOSED.auction_id]
    assert entry["status"] == "done" and entry["retrieved"] == 2500


def test_collect_hits_propagates_failure() -> None:
    fake = FakeAlgolia()
    fake.fail_auctions.add(CLOSED.auction_id)
    assert search_auction_lots(CLOSED, query=fake) is None


def test_list_auctions_per_year_newest_first() -> None:
    fake = FakeAlgolia(
        auctions=[
            _auction_hit(CLOSED, 1764806400),
            _auction_hit(OPEN, 1790640000),
            {"objectID": "bad", "slugYear": "2026"},
        ]
    )
    auctions = list_auctions(fake, first_year=2025, last_year=2026)
    assert [a.auction_id for a in auctions] == [OPEN.auction_id, CLOSED.auction_id]
    filters = [params["filters"] for _, params in fake.calls]
    assert filters == [
        'isTestRecord:false AND slugYear:"2025"',
        'isTestRecord:false AND slugYear:"2026"',
    ]


def test_list_auctions_raises_when_a_year_fails() -> None:
    with pytest.raises(RuntimeError):
        list_auctions(lambda *_: None, first_year=2025, last_year=2025)


def test_search_auction_lots_dedupes_and_applies_extra_filter() -> None:
    lot = _lot(CLOSED, 1, objectTypes=["Print"])
    fake = FakeAlgolia(
        lots={
            CLOSED.auction_id: [
                lot,
                dict(lot),
                _lot(CLOSED, 2, None, objectTypes=["Print"]),
                _lot(CLOSED, 3, objectTypes=["Wine"]),
            ]
        }
    )
    result = search_auction_lots(CLOSED, query=fake, extra_filter='(objectTypes:"Print")')
    assert result is not None
    entries, total = result
    assert total == 3
    assert [e.entity_id for e in entries] == ["2025/old-masters/lot-1"]
    assert fake.calls[0][1]["filters"].endswith('AND (objectTypes:"Print")')


def test_expand_full_mode_done_open_failed_and_sales(tmp_path: Path) -> None:
    state_path = tmp_path / "sothebys_algolia_browse_state.json"
    fake = FakeAlgolia(
        lots={
            CLOSED.auction_id: [_lot(CLOSED, 1), _lot(CLOSED, 2)],
            OPEN.auction_id: [_lot(OPEN, 1)],
        }
    )
    fake.fail_auctions.add(OPEN.auction_id)
    first = expand_lots_from_algolia(
        state_path=state_path, auctions=[CLOSED, OPEN], query=fake, workers=1
    )
    assert {e.entity_key for e in first} == {
        ("sale", "2025/old-masters"),
        ("lot", "2025/old-masters/lot-1"),
        ("lot", "2025/old-masters/lot-2"),
    }
    state = json.loads(state_path.read_text(encoding="utf-8"))["sales"]
    closed = state[CLOSED.auction_id]
    assert {k: closed[k] for k in ("status", "hits", "written")} == {
        "status": "done",
        "hits": 2,
        "written": 3,
    }
    assert (closed["expected"], closed["retrieved"], closed["partitions"]) == (2, 2, 1)
    assert state[OPEN.auction_id]["status"] == "failed"

    fake.fail_auctions.clear()
    fake.calls.clear()
    second = expand_lots_from_algolia(
        state_path=state_path, auctions=[CLOSED, OPEN], query=fake, workers=2
    )
    assert {params["filters"] for _, params in fake.calls} == {lot_filters(OPEN)}
    assert {e.entity_type for e in second} == {"sale", "lot"}
    state = json.loads(state_path.read_text(encoding="utf-8"))["sales"]
    assert state[OPEN.auction_id]["status"] == "open"

    fake.lots[OPEN.auction_id].append(_lot(OPEN, 2))
    third = expand_lots_from_algolia(state_path=state_path, auctions=[CLOSED, OPEN], query=fake)
    assert [e.entity_id for e in third] == ["2026/modern-day-auction-3/lot-2"]
    rows = list(iter_typed_cache_rows(lot_cache_path(state_path)))
    assert len(rows) == len({(r[0], r[1]) for r in rows}) == 6


def test_expand_art_mode_skips_sales_and_honours_max_sales(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    fake = FakeAlgolia(
        lots={
            CLOSED.auction_id: [
                _lot(CLOSED, 1, departments=["Prints"]),
                _lot(CLOSED, 2, departments=["Wine"]),
            ]
        }
    )
    art = build_art_filter(["Prints"])
    out = expand_lots_from_algolia(
        state_path=state_path,
        auctions=[OPEN, CLOSED],
        query=fake,
        art_filter=art,
        include_sales=False,
        max_sales=1,
    )
    assert [e.entity_type for e in out] == ["lot"]
    assert list(json.loads(state_path.read_text(encoding="utf-8"))["sales"]) == [
        CLOSED.auction_id
    ]
    assert fake.calls[0][1]["filters"] == lot_filters(CLOSED, art)


def test_expand_force_resets_state_and_cache(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    fake = FakeAlgolia(lots={CLOSED.auction_id: [_lot(CLOSED, 1)]})
    expand_lots_from_algolia(state_path=state_path, auctions=[CLOSED], query=fake)
    assert expand_lots_from_algolia(state_path=state_path, auctions=[CLOSED], query=fake) == []
    again = expand_lots_from_algolia(
        state_path=state_path, auctions=[CLOSED], query=fake, force=True
    )
    assert len(again) == 2
    assert len(list(iter_typed_cache_rows(lot_cache_path(state_path)))) == 2


def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("html_downloader.auctions.sothebys_algolia.time.sleep", lambda _s: None)


def test_client_fetches_key_via_graphql_and_sends_algolia_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _no_sleep(monkeypatch)
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if str(request.url) == GRAPHQL_URL:
            body = json.loads(request.content)
            assert body["variables"] == {"filters": []}
            return httpx.Response(200, json={"data": {"algoliaSearchKey": {"key": "K1"}}})
        return httpx.Response(200, json={"nbHits": 0, "hits": []})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = SothebysAlgoliaClient(delay=0, client=http)
    data = client.query(LOTS_INDEX, {"query": "", "filters": "isTestLot:false", "page": 0})
    assert data == {"nbHits": 0, "hits": []}
    algolia = seen[-1]
    assert algolia.url.path == f"/1/indexes/{LOTS_INDEX}/query"
    assert algolia.headers["X-Algolia-API-Key"] == "K1"
    assert algolia.headers["X-Algolia-Application-Id"] == "KAR1UEUPJD"
    params = parse_qs(json.loads(algolia.content)["params"])
    assert params["filters"] == ["isTestLot:false"]
    client.query(LOTS_INDEX, {"query": ""})
    assert sum(1 for r in seen if str(r.url) == GRAPHQL_URL) == 1


def test_client_refreshes_key_once_on_403(monkeypatch: pytest.MonkeyPatch) -> None:
    _no_sleep(monkeypatch)
    keys = iter(["stale", "fresh"])
    sent: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        key = request.headers["X-Algolia-API-Key"]
        sent.append(key)
        return httpx.Response(403) if key == "stale" else httpx.Response(200, json={"hits": []})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = SothebysAlgoliaClient(delay=0, client=http, fetch_key=lambda: next(keys))
    assert client.query(LOTS_INDEX, {"query": ""}) == {"hits": []}
    assert sent == ["stale", "fresh"]


def test_client_gives_up_on_persistent_403_and_hard_4xx(monkeypatch: pytest.MonkeyPatch) -> None:
    _no_sleep(monkeypatch)
    fetches: list[int] = []

    def fetch_key() -> str:
        fetches.append(1)
        return "k"

    forbidden = httpx.Client(transport=httpx.MockTransport(lambda _r: httpx.Response(403)))
    client = SothebysAlgoliaClient(delay=0, client=forbidden, retries=3, fetch_key=fetch_key)
    assert client.query(LOTS_INDEX, {"query": ""}) is None
    assert len(fetches) == 2

    bad = httpx.Client(transport=httpx.MockTransport(lambda _r: httpx.Response(400)))
    assert SothebysAlgoliaClient(delay=0, client=bad, api_key="k").query(LOTS_INDEX, {}) is None


def test_client_backs_off_on_429(monkeypatch: pytest.MonkeyPatch) -> None:
    _no_sleep(monkeypatch)
    responses = iter([httpx.Response(429), httpx.Response(200, json={"hits": [1]})])
    http = httpx.Client(transport=httpx.MockTransport(lambda _r: next(responses)))
    client = SothebysAlgoliaClient(delay=0, client=http, api_key="k")
    assert client.query(LOTS_INDEX, {}) == {"hits": [1]}

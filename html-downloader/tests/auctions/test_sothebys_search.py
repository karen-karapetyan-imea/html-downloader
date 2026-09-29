from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from itertools import pairwise
from pathlib import Path
from typing import Any

import pytest

from html_downloader.auctions import sothebys_search as search_mod
from html_downloader.auctions.algolia_partition import PartitionLog, partition_log_path
from html_downloader.auctions.search_state import SaleSearchState
from html_downloader.auctions.sitemap_cache import append_typed_cache
from html_downloader.auctions.sothebys_legacy import LegacyFetchError
from html_downloader.auctions.sothebys_search import (
    DEFAULT_SEARCH_CONFIG,
    FULL_TYPE_FILTER,
    NO_END_DATE_KEY,
    SearchWindow,
    SiteSearcher,
    all_windows,
    build_search_filter,
    collect_window,
    expand_site_search,
    fetch_search_config,
    hit_to_entry,
    lot_cache_path,
    month_windows,
    parse_search_config,
)
from html_downloader.discover.sitemap import SitemapEntry
from tests.auctions.algolia_fake import FakeIndex

NOW = datetime(2026, 9, 28, tzinfo=timezone.utc)
MARCH_2019 = int(datetime(2019, 3, 31, 5, tzinfo=timezone.utc).timestamp() * 1000)
NOV_2003 = int(datetime(2003, 11, 12, tzinfo=timezone.utc).timestamp() * 1000)


class FakeEngine(FakeIndex):
    """The site-search index; filtered ``nbHits`` is an estimate (no exhaustive flag)."""

    def query(self, index: str, params: dict[str, Any]) -> dict[str, Any] | None:
        assert index == DEFAULT_SEARCH_CONFIG.index
        assert params["distinct"] == "false"
        return super().query(index, params)


def _lot(
    n: int, end: int | None, estimate: int | None = 100, **extra: Any
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "type": "Lot",
        "url": f"https://www.sothebys.com/en/auctions/ecatalogue/2003/prints-n07888/lot.{n}.html",
        **extra,
    }
    if end is not None:
        record["endDate"] = end
    if estimate is not None:
        record["lowEstimate"] = estimate
    return record


@pytest.fixture(autouse=True)
def _small_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(search_mod, "MAX_HITS", 5)
    monkeypatch.setattr(search_mod, "CHUNK_BUDGET", 4)


def _window(key: str) -> SearchWindow:
    return next(w for w in all_windows(NOW) if w.key == key)


def _lot_numbers(hits: list[dict[str, Any]]) -> list[int]:
    return sorted(int(re.search(r"lot\.(\d+)", h["url"]).group(1)) for h in hits)


def test_parse_search_config_reads_embedded_values() -> None:
    body = (
        "window.cfg = { ALGOLIA_SEARCH_APP_ID: 'APP1', "
        "ALGOLIA_SEARCH_API_KEY: \"key-1\", ALGOLIA_SEARCH_INDEX: 'idx_en' }"
    )
    config = parse_search_config(body)
    assert config is not None
    assert (config.app_id, config.api_key, config.index) == ("APP1", "key-1", "idx_en")
    assert config.host == "https://app1-dsn.algolia.net"
    assert parse_search_config("ALGOLIA_SEARCH_APP_ID: 'APP1'") is None


def test_fetch_search_config_falls_back_to_defaults() -> None:
    def failing(_url: str) -> str | None:
        raise LegacyFetchError("blocked")

    assert fetch_search_config(failing) == DEFAULT_SEARCH_CONFIG
    assert (
        fetch_search_config(lambda _url: "<html>no config</html>")
        == DEFAULT_SEARCH_CONFIG
    )


def test_month_windows_are_contiguous_and_reach_ahead() -> None:
    windows = month_windows(NOW)
    assert windows[0].key == "1970-01"
    assert windows[-1].key == "2028-09"
    for left, right in pairwise(windows):
        assert left.end_ms is not None and right.start_ms is not None
        assert left.end_ms + 1 == right.start_ms
    assert all_windows(NOW)[-1] == SearchWindow(NO_END_DATE_KEY, None, None)


def test_window_settles_after_reopen_period() -> None:
    assert _window("2026-01").is_settled(NOW)
    assert not _window("2026-08").is_settled(NOW)
    assert not _window(NO_END_DATE_KEY).is_settled(NOW)


def test_build_search_filter_modes() -> None:
    assert build_search_filter(None) == FULL_TYPE_FILTER
    assert (
        build_search_filter(["Prints", "Prints", " "])
        == 'type:Lot AND (departments:"Prints")'
    )


def test_hit_to_entry_maps_lots_and_sales() -> None:
    legacy = hit_to_entry(
        {
            "url": "https://www.sothebys.com/en/auctions/ecatalogue/2003/Prints-N07888/lot.513.html",
            "endDate": NOV_2003,
        }
    )
    assert legacy is not None
    assert legacy.entity_key == ("lot", "legacy/2003/prints-n07888/513")
    assert legacy.lastmod == "2003-11-12"
    sale = hit_to_entry(
        {"url": "https://www.sothebys.com/en/buy/auction/2021/important-chinese-art"}
    )
    assert sale is not None and sale.entity_key == (
        "sale",
        "2021/important-chinese-art",
    )
    assert hit_to_entry({"url": "https://www.sothebys.com/en/articles/a-story"}) is None
    assert hit_to_entry({"url": None}) is None


@pytest.mark.parametrize("facets", [True, False])
def test_collect_window_enumerates_dense_month(facets: bool) -> None:
    records = [_lot(n, MARCH_2019 + n * 60_000) for n in range(1, 13)]
    # One sale closing in a single millisecond: needs the lowEstimate split.
    records += [_lot(100 + n, MARCH_2019, estimate=n * 10) for n in range(9)]
    records += [
        _lot(200, MARCH_2019, estimate=None),
        _lot(201, MARCH_2019, estimate=None),
    ]
    records.append(_lot(300, NOV_2003))
    engine = FakeEngine(records, facets=facets)
    hits = collect_window(
        SiteSearcher(engine.query, DEFAULT_SEARCH_CONFIG.index),
        FULL_TYPE_FILTER,
        _window("2019-03"),
    )
    assert hits is not None
    assert _lot_numbers(hits) == [*range(1, 13), *range(100, 109), 200, 201]


def test_collect_window_without_end_date() -> None:
    engine = FakeEngine([_lot(1, None), _lot(2, NOV_2003)])
    hits = collect_window(
        SiteSearcher(engine.query, DEFAULT_SEARCH_CONFIG.index),
        FULL_TYPE_FILTER,
        _window(NO_END_DATE_KEY),
    )
    assert hits is not None and _lot_numbers(hits) == [1]


def test_collect_window_without_end_date_splits_past_the_cap() -> None:
    records = [_lot(n, None, departments=["Prints" if n % 2 else "Wine"]) for n in range(9)]
    records += [_lot(20 + n, None, departments=None) for n in range(3)]
    records += [
        {"type": "Auction", "url": f"https://www.sothebys.com/en/buy/auction/2021/sale-{n}"}
        for n in range(2)
    ]
    engine = FakeEngine(records)
    hits = collect_window(
        SiteSearcher(engine.query, DEFAULT_SEARCH_CONFIG.index),
        FULL_TYPE_FILTER,
        _window(NO_END_DATE_KEY),
    )
    assert hits is not None
    assert len(hits) == 14


def test_single_millisecond_overflow_falls_through_to_later_fields() -> None:
    # Same endDate and estimate: only ``locations`` can separate these lots.
    records = [
        _lot(n, MARCH_2019, estimate=500, locations=[("London", "Paris", None)[n % 3]])
        for n in range(12)
    ]
    for record in records:
        if record["locations"] == [None]:
            del record["locations"]
    engine = FakeEngine(records)
    hits = collect_window(
        SiteSearcher(engine.query, DEFAULT_SEARCH_CONFIG.index),
        FULL_TYPE_FILTER,
        _window("2019-03"),
    )
    assert hits is not None and _lot_numbers(hits) == list(range(12))
    assert any('locations:"London"' in p["filters"] for p in engine.calls)


def test_unsplittable_window_is_incomplete_not_done(tmp_path: Path) -> None:
    engine = FakeEngine([_lot(n, NOV_2003, estimate=500) for n in range(8)])
    state_path = tmp_path / "sothebys_site_search_state.json"
    expand_site_search(state_path=state_path, query=engine.query, now=NOW, max_windows=1000)
    entry = SaleSearchState(state_path).get("2003-11")
    assert entry is not None and entry["status"] == "incomplete"
    assert entry["incomplete"] == 1 and entry["retrieved"] == 5
    leaves = PartitionLog(partition_log_path(state_path)).latest()["2003-11"]
    assert [r["reason"] for r in leaves if r["kind"] == "leaf" and not r["complete"]] == [
        "unsplittable"
    ]


def test_collect_window_propagates_failure() -> None:
    engine = FakeEngine([_lot(n, MARCH_2019 + n) for n in range(20)])
    engine.fail_after = 2  # window query + facet plan succeed, first chunk fails
    searcher = SiteSearcher(engine.query, DEFAULT_SEARCH_CONFIG.index)
    assert collect_window(searcher, FULL_TYPE_FILTER, _window("2019-03")) is None


def _cache_keys(path: Path) -> list[tuple[str, str]]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    return [(row["entity_type"], row["entity_id"]) for row in rows]


def test_expand_site_search_checkpoints_and_skips_known_keys(tmp_path: Path) -> None:
    state_path = tmp_path / "sothebys_site_search_state.json"
    other_cache = tmp_path / "sothebys_legacy_state_lots.jsonl"
    append_typed_cache(
        other_cache,
        [
            SitemapEntry(
                url="https://www.sothebys.com/en/auctions/ecatalogue/2003/prints-n07888/lot.2.html",
                lastmod=None,
                entity_type="lot",
                entity_id="legacy/2003/prints-n07888/2",
            )
        ],
    )
    recent = int(datetime(2026, 9, 1, tzinfo=timezone.utc).timestamp() * 1000)
    engine = FakeEngine([_lot(1, NOV_2003), _lot(2, NOV_2003), _lot(3, recent)])

    entries = expand_site_search(
        state_path=state_path,
        query=engine.query,
        known_cache_paths=[other_cache],
        now=NOW,
        workers=3,
    )

    assert {e.entity_id for e in entries} == {
        "legacy/2003/prints-n07888/1",
        "legacy/2003/prints-n07888/3",
    }
    assert sorted(_cache_keys(lot_cache_path(state_path))) == [
        ("lot", "legacy/2003/prints-n07888/1"),
        ("lot", "legacy/2003/prints-n07888/3"),
    ]
    state = SaleSearchState(state_path)
    assert state.status("2003-11") == "done"
    assert state.status("2026-09") == "open"
    assert state.status(NO_END_DATE_KEY) == "open"

    engine.calls.clear()
    assert expand_site_search(state_path=state_path, query=engine.query, now=NOW) == []
    queried = {p["filters"] for p in engine.calls}
    assert not any(f"endDate >= {_window('2003-11').start_ms} AND" in f for f in queried)
    assert len(_cache_keys(lot_cache_path(state_path))) == 2


def test_expand_site_search_marks_failures_and_force_resets(tmp_path: Path) -> None:
    state_path = tmp_path / "sothebys_site_search_state.json"
    engine = FakeEngine([_lot(1, NOV_2003)])
    engine.fail_when = f"endDate >= {_window('2003-11').start_ms} AND"
    expand_site_search(state_path=state_path, query=engine.query, now=NOW)
    assert SaleSearchState(state_path).status("2003-11") == "failed"
    assert not lot_cache_path(state_path).exists()

    engine.fail_when = None
    entries = expand_site_search(state_path=state_path, query=engine.query, now=NOW)
    assert [e.entity_id for e in entries] == ["legacy/2003/prints-n07888/1"]

    entries = expand_site_search(
        state_path=state_path, query=engine.query, now=NOW, force=True
    )
    assert [e.entity_id for e in entries] == ["legacy/2003/prints-n07888/1"]
    assert len(_cache_keys(lot_cache_path(state_path))) == 1


def test_expand_site_search_art_mode_and_max_windows(tmp_path: Path) -> None:
    engine = FakeEngine(
        [
            _lot(1, NOV_2003, departments=["Prints"]),
            _lot(2, NOV_2003, departments=["Wine"]),
        ]
    )
    state_path = tmp_path / "sothebys_site_search_artworks_state.json"
    entries = expand_site_search(
        state_path=state_path,
        query=engine.query,
        departments=["Prints"],
        now=NOW,
        max_windows=len(month_windows(NOW)),
    )
    assert [e.entity_id for e in entries] == ["legacy/2003/prints-n07888/1"]
    assert SaleSearchState(state_path).status(NO_END_DATE_KEY) is None
    assert all(
        p["filters"].startswith('type:Lot AND (departments:"Prints")')
        for p in engine.calls
    )

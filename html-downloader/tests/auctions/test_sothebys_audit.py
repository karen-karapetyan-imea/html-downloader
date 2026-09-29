from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from html_downloader.auctions.algolia_partition import (
    CollectSummary,
    LeafRecord,
    PartitionLog,
    partition_log_path,
)
from html_downloader.auctions.search_state import SaleSearchState
from html_downloader.auctions.sitemap_cache import append_typed_cache, typed_cache_path
from html_downloader.auctions.sothebys_algolia import (
    AUCTIONS_INDEX,
    SothebysAuction,
)
from html_downloader.auctions.sothebys_audit import (
    AuditSources,
    build_report,
    cache_stats,
    format_report,
    legacy_stats,
    overlap_stats,
    pick_sample,
    sample_test_lots,
    scan_caches,
    window_source_stats,
)
from html_downloader.auctions.sothebys_legacy import LISTING_KEY
from html_downloader.auctions.sothebys_search import DEFAULT_SEARCH_CONFIG
from html_downloader.discover.sitemap import SitemapEntry
from tests.auctions.algolia_fake import respond

NOW = datetime(2026, 9, 28, tzinfo=timezone.utc)
NOV_2003 = int(datetime(2003, 11, 12, tzinfo=timezone.utc).timestamp() * 1000)
AUCTION = SothebysAuction("aaaa-1", "2025", "old-masters", "Closed", "2025-12-04")


def _platform_lot(n: int) -> SitemapEntry:
    return SitemapEntry(
        url=f"https://www.sothebys.com/en/buy/auction/2025/old-masters/lot-{n}",
        lastmod=None,
        entity_type="lot",
        entity_id=f"2025/old-masters/lot-{n}",
    )


def _legacy_lot(n: int, sale: str = "2003/prints-n07888") -> SitemapEntry:
    year, slug = sale.split("/")
    return SitemapEntry(
        url=f"https://www.sothebys.com/en/auctions/ecatalogue/{year}/{slug}/lot.{n}.html",
        lastmod=None,
        entity_type="lot",
        entity_id=f"legacy/{sale}/{n}",
    )


def _sources(tmp_path: Path) -> AuditSources:
    return AuditSources(
        platform_state=tmp_path / "sothebys_algolia_browse_state.json",
        legacy_state=tmp_path / "sothebys_legacy_state.json",
        site_search_state=tmp_path / "sothebys_site_search_state.json",
    )


def test_overlap_stats_pairs_triple_and_union() -> None:
    stats = overlap_stats({"a": {1, 2, 3, 4}, "b": {3, 4, 5}, "c": {4, 5, 6}})
    assert stats["sizes"] == {"a": 4, "b": 3, "c": 3}
    assert stats["pairs"] == {"a & b": 2, "a & c": 1, "b & c": 2}
    assert stats["all"] == {"a & b & c": 1}
    assert (stats["union"], stats["sum"]) == (6, 10)


def test_cache_stats_detects_non_disjoint_caches(tmp_path: Path) -> None:
    sources = _sources(tmp_path)
    append_typed_cache(typed_cache_path(sources.platform_state), [_platform_lot(1)])
    append_typed_cache(typed_cache_path(sources.site_search_state), [_legacy_lot(1)])
    append_typed_cache(typed_cache_path(sources.legacy_state), [_legacy_lot(2)])
    clean = cache_stats(scan_caches(sources.states()))
    assert clean["disjoint"] and clean["total_unique"] == 3

    append_typed_cache(typed_cache_path(sources.legacy_state), [_legacy_lot(1)])
    dirty = cache_stats(scan_caches(sources.states()))
    assert not dirty["disjoint"]
    assert dirty["cross_cache_overlap"]["legacy & site_search"] == 1
    assert (dirty["sum_unique"], dirty["total_unique"]) == (4, 3)


def test_legacy_stats_counts_short_sales_across_caches(tmp_path: Path) -> None:
    sources = _sources(tmp_path)
    state = SaleSearchState(sources.legacy_state)  # type: ignore[arg-type]
    state.mark(LISTING_KEY, "done", hits=2)
    state.mark("2003/prints-n07888", "done", hits=3, written=3)
    state.mark("2004/wine-l04000", "done", hits=4, written=2)
    state.mark("2004/gone-l04001", "missing")
    state.flush()
    append_typed_cache(
        typed_cache_path(sources.legacy_state),  # type: ignore[arg-type]
        [_legacy_lot(1), _legacy_lot(2), _legacy_lot(1, "2004/wine-l04000")],
    )
    append_typed_cache(typed_cache_path(sources.site_search_state), [_legacy_lot(3)])
    stats = legacy_stats(sources.legacy_state, scan_caches(sources.states()))  # type: ignore[arg-type]
    assert stats["sales"] == 3
    assert stats["statuses"] == {"done": 2, "missing": 1}
    assert (stats["expected_lots"], stats["generated_urls"]) == (7, 3)
    assert (stats["short_sales"], stats["short_lots"]) == (1, 3)
    assert stats["short_sale_samples"][0]["sale"] == "2004/wine-l04000"


def test_window_source_stats_reads_extras_and_partition_log(tmp_path: Path) -> None:
    path = tmp_path / "sothebys_algolia_browse_state.json"
    state = SaleSearchState(path)
    state.mark("old", "done", hits=10, written=11)
    state.mark(
        "new", "incomplete", hits=5, written=5, expected=8, expected_exact=True,
        retrieved=5, urls=5, partitions=3, incomplete=1, url="https://x",
    )
    state.flush()
    run = PartitionLog(partition_log_path(path)).start("new")
    run.record_leaf(
        LeafRecord("k", "f", 8, True, 5, 5, 5, 0, complete=False, reason="unsplittable")
    )
    run.finish("incomplete", CollectSummary())
    stats = window_source_stats(path)
    assert (stats["windows"], stats["pre_audit_windows"], stats["audited_windows"]) == (2, 1, 1)
    assert (stats["expected_hits"], stats["retrieved_hits"]) == (8, 15)
    assert stats["incomplete_partitions"] == 1
    assert stats["incomplete_partition_samples"][0]["reason"] == "unsplittable"
    assert stats["shortfall_window_count"] == 1
    assert stats["not_complete_windows"][0]["window"] == "new"


def test_pick_sample_mixes_largest_and_spread() -> None:
    auctions = [
        SothebysAuction(f"id-{i}", "2020", f"s-{i}", "Closed", f"2020-01-{i + 1:02d}")
        for i in range(20)
    ]
    picked = pick_sample(auctions, {"id-7": 900, "id-3": 800}, 8)
    assert len(picked) == 8
    assert picked[0].auction_id == "id-7"
    assert pick_sample(auctions, {}, 0) == []


def test_sample_test_lots_counts_exactly_per_auction() -> None:
    rows = [
        {"auctionId": AUCTION.auction_id, "isTestLot": False},
        {"auctionId": AUCTION.auction_id, "isTestLot": True},
        {"auctionId": AUCTION.auction_id, "isTestLot": True},
        {"auctionId": "other", "isTestLot": False},
    ]
    sample = sample_test_lots(lambda _i, p: respond(rows, p, exact=True), [AUCTION])
    assert (sample["total"], sample["test"], sample["real"]) == (3, 2, 1)
    assert sample["rows"][0]["exact"] is True


def test_build_report_offline_and_recount_overlap(tmp_path: Path) -> None:
    sources = _sources(tmp_path)
    append_typed_cache(typed_cache_path(sources.platform_state), [_platform_lot(1)])
    append_typed_cache(typed_cache_path(sources.legacy_state), [_legacy_lot(1)])  # type: ignore[arg-type]
    append_typed_cache(typed_cache_path(sources.site_search_state), [_legacy_lot(2)])  # type: ignore[arg-type]
    urls = tmp_path / "urls.txt"
    urls.write_text("a\nb\nc\n")

    offline = build_report(sources, urls_path=urls)
    assert offline["caches"]["disjoint"] and offline["urls_txt"]["matches_total_unique"]
    assert "index_totals" not in offline
    assert "Total unique URLs" in format_report(offline)

    auctions = [
        {
            "objectID": AUCTION.auction_id,
            "slugYear": "2025",
            "slugName": "old-masters",
            "state": "Closed",
            "isTestRecord": False,
            "auctionDates": {"endDate": 1764806400},
        }
    ]
    lots = [
        {"auctionId": AUCTION.auction_id, "isTestLot": False, "lotNr": n,
         "slug": f"/en/buy/auction/2025/old-masters/lot-{n}"}
        for n in (1, 2)
    ]

    def platform(index: str, params: dict[str, Any]) -> dict[str, Any]:
        return respond(auctions if index == AUCTIONS_INDEX else lots, params, exact=True)

    site_rows = [
        {"type": "Lot", "endDate": NOV_2003, "url": _legacy_lot(n).url} for n in (1, 2, 3)
    ] + [{"type": "Lot", "endDate": NOV_2003, "url": _platform_lot(1).url}]

    def site(_index: str, params: dict[str, Any]) -> dict[str, Any]:
        return respond(site_rows, params)

    report = build_report(
        sources,
        platform_query=platform,
        site_query=site,
        site_index=DEFAULT_SEARCH_CONFIG.index,
        do_recount=True,
        sample_size=1,
        now=NOW,
    )
    recount = report["recount"]
    overlap = recount["overlap"]
    # platform raw: lot-1, lot-2, sale; legacy raw: legacy 1; site raw: legacy 1-3 + lot-1
    assert overlap["sizes"] == {"platform": 3, "legacy": 1, "site_search": 4}
    assert overlap["pairs"] == {
        "platform & legacy": 0,
        "platform & site_search": 1,
        "legacy & site_search": 1,
    }
    assert overlap["all"] == {"platform & legacy & site_search": 0}
    assert overlap["union"] == 6 and overlap["cache_union"] == 3
    assert overlap["raw_not_in_caches"] == 3  # lot-2, the sale page, legacy lot 3
    assert recount["platform"]["missing_from_caches"] == 2
    assert recount["site_search"]["missing_from_caches"] == 1
    assert report["test_lots"]["real"] == 2
    assert report["index_totals"]["platform"]["lots"]["nb_hits"] == 2
    assert "Cross-source overlap" in format_report(report)
